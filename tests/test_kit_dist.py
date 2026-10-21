"""The mcp-ops-kit distribution (scripts/build_kit.py, scripts/check_kit_dist.py).

CI's ``package`` job builds the real sdist and wheel, checks them and runs the smoke test
in a fresh venv. These tests need no build tool: they pin the README link rewrite, the
dependency declarations, and that the checker refuses a distribution broken one way at a
time.
"""

import ast
import importlib.metadata as md
import io
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

from scripts import build_kit, check_kit_dist

ROOT = Path(__file__).resolve().parent.parent
KIT_PYPROJECT = ROOT / "packaging" / "mcp-ops-kit" / "pyproject.toml"
REPO = build_kit.REPO_URL


def test_relative_readme_links_become_absolute() -> None:
    text = (
        "see [log](../FAILURES.md), [salon](../salon_ops/), [seed](../salon_ops/seed.py), "
        "[here](#guarantees), [ext](https://example.org/x), [same](./auth.py)"
    )
    out = build_kit.absolute_links(text, "kit-v0.1.0")
    assert f"[log]({REPO}/blob/kit-v0.1.0/FAILURES.md)" in out
    assert f"[salon]({REPO}/tree/kit-v0.1.0/salon_ops/)" in out
    assert f"[seed]({REPO}/blob/kit-v0.1.0/salon_ops/seed.py)" in out
    assert f"[same]({REPO}/blob/kit-v0.1.0/mcp_ops_kit/auth.py)" in out
    assert "[here](#guarantees)" in out and "[ext](https://example.org/x)" in out


def test_a_link_out_of_the_repo_is_refused() -> None:
    with pytest.raises(ValueError, match="leaves the repo"):
        build_kit.absolute_links("[x](../../elsewhere.md)")


def test_the_staged_readme_has_no_relative_link(tmp_path: Path) -> None:
    build_kit.stage(tmp_path)
    readme = (tmp_path / "README.md").read_text()
    assert build_kit.RELATIVE_LINK.findall(readme) == []
    assert (tmp_path / "mcp_ops_kit" / "__init__.py").exists()
    assert not (tmp_path / "mcp_ops_kit" / "README.md").exists()


def test_a_tag_for_another_version_cannot_build(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not 9.9.9"):
        build_kit.main(["--out", str(tmp_path / "d"), "--expect-version", "9.9.9"])


def test_the_kit_declares_the_same_dependencies_as_the_repo() -> None:
    """Two pyproject files carry the kit; they must not drift apart."""
    kit = tomllib.loads(KIT_PYPROJECT.read_text())["project"]
    repo = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert kit["dependencies"] == repo["dependencies"]
    assert kit["optional-dependencies"]["live"] == repo["optional-dependencies"]["live"]
    assert kit["requires-python"] == repo["requires-python"]


def _closure(names: list[str]) -> set[str]:
    """Every distribution pip installs for these requirements (no extras)."""
    seen: set[str] = set()
    todo = [re.match(r"[A-Za-z0-9_.\-]+", n).group(0).lower().replace("_", "-") for n in names]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            requires = md.requires(name) or []
        except md.PackageNotFoundError:
            continue  # excluded by its marker (e.g. python_version < "3.11"), so not installed
        for req in requires:
            if "extra ==" in req or "sys_platform == 'win32'" in req:
                continue
            todo.append(re.match(r"[A-Za-z0-9_.\-]+", req).group(0).lower().replace("_", "-"))
    return seen


def test_every_import_of_the_kit_is_installed_with_it() -> None:
    """``pip install mcp-ops-kit`` must bring every module the kit imports (the optional
    OpenAI agent's ``openai`` comes with the ``live`` extra)."""
    kit = tomllib.loads(KIT_PYPROJECT.read_text())["project"]
    installed = _closure(kit["dependencies"])
    live = _closure(kit["optional-dependencies"]["live"])
    providers = md.packages_distributions()
    missing = []
    for path in (ROOT / "mcp_ops_kit").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            else:
                continue
            for top in {m.split(".")[0] for m in mods}:
                if top in sys.stdlib_module_names or top == "mcp_ops_kit":
                    continue
                dists = {d.lower().replace("_", "-") for d in providers.get(top, [])}
                pool = live if top == "openai" else installed
                if not dists & pool:
                    missing.append(f"{path.name}: {top}")
    assert missing == []


# A synthetic distribution, written to look like hatchling's, then broken one way at a time.


def _metadata(version: str, requires: list[str]) -> str:
    lines = ["Metadata-Version: 2.4", "Name: mcp-ops-kit", f"Version: {version}"]
    return "\n".join(lines + [f"Requires-Dist: {r}" for r in requires]) + "\n\n"


GOOD_REQUIRES = ["cryptography>=42", "mcp<3,>=2.3", "openai<4,>=3.35; extra == 'live'"]


def _write(
    dist: Path,
    *,
    version: str = "0.1.0",
    add: tuple[str, ...] = (),
    drop: tuple[str, ...] = (),
    requires: list[str] = GOOD_REQUIRES,
    sdist_add: tuple[str, ...] = (),
) -> Path:
    dist.mkdir()
    files = (check_kit_dist.kit_files() | set(add)) - set(drop)
    info = f"mcp_ops_kit-{version}.dist-info/"
    with zipfile.ZipFile(dist / f"mcp_ops_kit-{version}-py3-none-any.whl", "w") as zf:
        for name in files:
            zf.writestr(name, "")
        zf.writestr(info + "METADATA", _metadata(version, requires))
        zf.writestr(info + "RECORD", "")
    top = f"mcp_ops_kit-{version}/"
    sdist_files = files | {"README.md", "LICENSE", "pyproject.toml", "PKG-INFO", *sdist_add}
    with tarfile.open(dist / f"mcp_ops_kit-{version}.tar.gz", "w:gz") as tf:
        for name in sdist_files:
            member = tarfile.TarInfo(top + name)
            tf.addfile(member, io.BytesIO(b""))
    return dist


def test_a_well_formed_distribution_passes(tmp_path: Path) -> None:
    assert check_kit_dist.check(_write(tmp_path / "d")) == []


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"add": ("salon_ops/tools.py",)}, "must not contain salon_ops/tools.py"),
        ({"add": ("mcp_ops_server/server.py",)}, "domain or repo-only file mcp_ops_server"),
        ({"add": ("tests/test_kit.py",)}, "must not contain tests/test_kit.py"),
        ({"drop": ("mcp_ops_kit/audit.py",)}, "wheel is missing mcp_ops_kit/audit.py"),
        ({"version": "0.0.9"}, "not mcp_ops_kit 0.1.0"),
        ({"requires": ["mcp<3,>=2.3"]}, "differs from pyproject"),
        ({"requires": [*GOOD_REQUIRES, "requests"]}, "differs from pyproject"),
        ({"sdist_add": ("evals/golden/tasks.json",)}, "sdist must not contain evals/golden"),
    ],
)
def test_a_broken_distribution_is_refused(tmp_path: Path, kwargs: dict, expected: str) -> None:
    problems = check_kit_dist.check(_write(tmp_path / "d", **kwargs))
    assert any(expected in p for p in problems), problems


def test_a_stray_file_in_the_dist_folder_is_refused(tmp_path: Path) -> None:
    dist = _write(tmp_path / "d")
    (dist / "mcp_ops_kit-0.1.0-py3-none-any.whl.bak").write_text("")
    assert check_kit_dist.check(dist)[0].startswith("expected one wheel and one sdist")
