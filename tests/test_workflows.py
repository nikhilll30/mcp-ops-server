"""The CI workflows' own supply chain: what runs is pinned, and write access stays narrow.

Text checks (no YAML library is a dependency): every action is pinned to a commit SHA with
its version in a comment (Dependabot bumps both), every container image a workflow runs is
pinned by digest, every pin Dependabot cannot see is listed in docs/pinned-tools.md, and only
the two publishing jobs can mint an OIDC token or write packages, at workflow or job level.
Checks of what a workflow does read its code with comments removed, so a guard that was
commented out does not count (Codex, PR #24).
"""

import re
from pathlib import Path

import pytest

from scripts import check_release_ci

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", re.M)
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
IMAGE_HOSTS = ("ghcr.io/", "registry:", "hapiproject/", "docker.io/", "mirror.gcr.io/")


def _code(text: str) -> str:
    """The workflow without comments: comment lines dropped, ``  # ...`` tails cut."""
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(re.sub(r"\s+#\s.*$", "", line) for line in lines) + "\n"


def _jobs(text: str) -> dict[str, str]:
    """Each job's text, split at the two-space-indented keys under ``jobs:``."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _header(text: str) -> str:
    """Everything before ``jobs:``: triggers, workflow-level permissions, env."""
    return text.split("\njobs:\n", 1)[0]


def test_there_are_workflows_to_check() -> None:
    names = {p.name for p in WORKFLOWS}
    assert {"ci.yml", "codeql.yml", "release-kit.yml", "live-evals.yml"} <= names


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit(path: Path) -> None:
    uses = USES.findall(path.read_text())
    assert uses, f"{path.name} uses no action?"
    bad = [ref for ref, rest in uses if not PINNED.fullmatch(ref)]
    bad += [ref for ref, rest in uses if not re.fullmatch(r"\s+# v\d+\.\d+\.\d+", rest)]
    assert bad == []


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_image_a_workflow_runs_is_pinned_by_digest(path: Path) -> None:
    bad = []
    for token in re.findall(r"\S+", path.read_text()):
        token = token.strip("\"'")
        if any(h in token for h in IMAGE_HOSTS) and "${" not in token and "://" not in token:
            if not re.search(r"@sha256:[0-9a-f]{64}$", token):
                bad.append(token)
    assert bad == []


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_default_token_is_read_only(path: Path) -> None:
    """The workflow-level block is exactly ``contents: read``: anything more there would be
    inherited by every job that does not set its own."""
    block = re.search(r"^permissions:\n((?:  .*\n)*)", _header(_code(path.read_text())), re.M)
    assert block is not None and block.group(1) == "  contents: read\n"


def test_only_the_publishing_jobs_can_sign_or_write_packages() -> None:
    grants = {}
    for path in WORKFLOWS:
        code = _code(path.read_text())
        scopes = {"(workflow)": _header(code), **_jobs(code)}
        for job, text in scopes.items():
            for perm in ("id-token: write", "packages: write", "security-events: write"):
                if perm in text:
                    grants.setdefault(perm, set()).add(f"{path.name}:{job}")
            assert "contents: write" not in text, f"{path.name}:{job}"
    assert grants == {
        "id-token: write": {"ci.yml:publish", "release-kit.yml:publish"},
        "packages: write": {"ci.yml:publish"},
        "security-events: write": {"codeql.yml:analyze"},
    }


def test_publishing_runs_only_from_main_or_a_kit_tag() -> None:
    ci = _jobs(_code((ROOT / ".github" / "workflows" / "ci.yml").read_text()))
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in ci["publish"]
    release = _code((ROOT / ".github" / "workflows" / "release-kit.yml").read_text())
    assert re.search(r"^on:\n  push:\n    tags: \[\"kit-v\*\"\]\n\n", release, re.M)
    jobs = _jobs(release)
    assert "environment:\n      name: pypi" in jobs["publish"]
    assert "needs: build" in jobs["publish"]


def test_the_release_guards_are_code_not_comments() -> None:
    """Both guards run, in this order, before anything is built (comments do not count)."""
    build = _jobs(_code((ROOT / ".github" / "workflows" / "release-kit.yml").read_text()))["build"]
    ancestry = 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main ||'
    ci_green = 'run: python scripts/check_release_ci.py "$GITHUB_REPOSITORY" "$GITHUB_SHA"'
    assert ancestry in build and ci_green in build
    assert build.index(ancestry) < build.index(ci_green) < build.index("scripts/build_kit.py")
    assert "checks: read" in build


def test_a_release_requires_every_ci_job_and_codeql() -> None:
    ci = _code((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    jobs = _jobs(ci)
    assert not any(re.search(r"^    name:", text, re.M) for text in jobs.values()), (
        "a display name would change the check run's name; check_release_ci matches job IDs"
    )
    expected = [j for j in jobs if j != "publish"] + ["analyze (python)", "analyze (actions)"]
    assert check_release_ci.required_jobs() == expected


def _run(name: str, status: str = "completed", conclusion: str | None = "success", **kw) -> dict:
    run = {"name": name, "status": status, "conclusion": conclusion, "id": 1}
    return run | {"app": {"slug": "github-actions"}, "started_at": "2026-10-21T12:00:00Z"} | kw


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([_run("lint"), _run("test")], []),
        ([_run("lint")], ["test: no CI run on this commit"]),
        (
            [_run("lint"), _run("test", status="in_progress", conclusion=None)],
            ["test: still in_progress"],
        ),
        ([_run("lint"), _run("test", conclusion="failure")], ["test: failure"]),
        ([_run("lint"), _run("test", conclusion="skipped")], ["test: skipped"]),
        # the newest run decides: a re-run that failed after a pass blocks the release
        (
            [
                _run("lint"),
                _run("test"),
                _run("test", conclusion="failure", id=2, started_at="2026-10-21T13:00:00Z"),
            ],
            ["test: failure"],
        ),
        # a run of the same name from another app is not CI
        (
            [_run("lint"), _run("test", app={"slug": "someone-else"})],
            ["test: no CI run on this commit"],
        ),
    ],
)
def test_the_release_ci_check(runs: list[dict], expected: list[str]) -> None:
    assert check_release_ci.problems(runs, ["lint", "test"]) == expected


def test_pins_dependabot_cannot_see_are_listed() -> None:
    """Images run in shell steps or as services, binaries pinned by version, and pip pins
    inside a workflow: each must be in docs/pinned-tools.md, or (pip) not exist at all."""
    listed = (ROOT / "docs" / "pinned-tools.md").read_text()
    text = "".join(p.read_text() for p in WORKFLOWS)
    text += (ROOT / "scripts" / "install_supply_chain_tools.sh").read_text()
    images = {m.split("/")[-1] for m in re.findall(r"([\w./-]+):[\w.-]+@sha256:", text)}
    aliases = {"tf": "terraform"}  # TF_VERSION in ci.yml
    found = {m.lower() for m in re.findall(r"\b([A-Z]+)_VERSION[:=]", text)}
    binaries = {aliases.get(n, n) for n in found}
    assert images and binaries
    missing = sorted(n for n in images | binaries if f"| {n} |" not in listed)
    assert missing == []
    assert not re.search(r"pip install [^\n]*==", _code(text)), "pin in .github/requirements/"


def test_the_publish_job_signs_what_it_pushed_and_verifies_it() -> None:
    publish = _jobs((ROOT / ".github" / "workflows" / "ci.yml").read_text())["publish"]
    order = [
        publish.index('docker push "$IMAGE:sha-'),
        publish.index('scripts/supply_chain.sh sign "$IMAGE@$DIGEST"'),
        publish.index('scripts/supply_chain.sh verify "$IMAGE@$DIGEST"'),
        publish.index('docker push "$IMAGE:latest"'),
    ]
    assert order == sorted(order), "push, sign, verify, then move latest"


def test_supply_chain_tools_are_exact_versions() -> None:
    text = (ROOT / "scripts" / "install_supply_chain_tools.sh").read_text()
    assert re.search(r"^COSIGN_VERSION=v\d+\.\d+\.\d+$", text, re.M)
    assert re.search(r"^SYFT_VERSION=v\d+\.\d+\.\d+$", text, re.M)


def test_the_image_has_no_setuid_binaries_and_ci_checks_it() -> None:
    """Fargate cannot set no-new-privileges, so the image itself must hold nothing that
    could raise a process's privileges; CI scans the built image as root (Codex, PR #24)."""
    docker = _code((ROOT / "Dockerfile").read_text())
    assert "find / -xdev -perm /6000 -type f -exec chmod a-s {} +" in docker
    container = _jobs(_code((ROOT / ".github" / "workflows" / "ci.yml").read_text()))["container"]
    scan = container.index("docker run --rm --user 0 --entrypoint sh mcp-ops-server:ci")
    assert "find / -xdev -perm /6000 -type f" in container[scan : scan + 200]
    assert scan < container.index("Smoke test against the container")
