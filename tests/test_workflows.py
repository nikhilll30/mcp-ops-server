"""The CI workflows' own supply chain: what runs is pinned, and write access stays narrow.

Text checks (no YAML library is a dependency): every action is pinned to a commit SHA with
its version in a comment (Dependabot bumps both), every container image a workflow runs is
pinned by digest, and only the two publishing jobs can mint an OIDC token or write packages.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", re.M)
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
IMAGE_HOSTS = ("ghcr.io/", "registry:", "hapiproject/", "docker.io/", "mirror.gcr.io/")


def _jobs(text: str) -> dict[str, str]:
    """Each job's text, split at the two-space-indented keys under ``jobs:``."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


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
    head = path.read_text().split("\njobs:\n", 1)[0]
    assert re.search(r"^permissions:\n  contents: read\n", head, re.M)


def test_only_the_publishing_jobs_can_sign_or_write_packages() -> None:
    grants = {}
    for path in WORKFLOWS:
        for job, text in _jobs(path.read_text()).items():
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
    ci = _jobs((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in ci["publish"]
    release = (ROOT / ".github" / "workflows" / "release-kit.yml").read_text()
    assert re.search(r"^on:\n  push:\n    tags: \[\"kit-v\*\"\]\n\n", release, re.M)
    jobs = _jobs(release)
    assert "environment:\n      name: pypi" in jobs["publish"]
    assert "merge-base --is-ancestor" in jobs["build"]


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
