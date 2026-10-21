"""Builds the mcp-ops-kit distribution (sdist and wheel) from this repo's mcp_ops_kit/.

    python scripts/build_kit.py                       # into dist/kit/
    python scripts/build_kit.py --out DIR --expect-version 0.1.0

The kit is not built in place, because the repo root's pyproject.toml is the image's
distribution. This copies ``packaging/mcp-ops-kit/pyproject.toml``, ``mcp_ops_kit/``,
``LICENSE`` and the kit's README into a fresh staging folder and runs ``python -m build``
there, so the sdist holds everything its wheel needs and the wheel is built from the sdist
(what pip does with an sdist). The README's relative links are made absolute, since PyPI
shows them with no repo to resolve them against. Needs ``build`` (``pip install build``).

``--expect-version`` fails unless ``mcp_ops_kit.__version__`` is that version: the release
workflow passes the version from the tag, so a tag cannot publish another version.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "mcp_ops_kit"
REPO_URL = "https://github.com/nikhilll30/mcp-ops-server"
VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.M)
# A markdown link target that is not a URL or an in-page anchor.
RELATIVE_LINK = re.compile(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)")


def kit_version(kit: Path = KIT) -> str:
    match = VERSION.search((kit / "__init__.py").read_text())
    if match is None:
        raise SystemExit("mcp_ops_kit/__init__.py has no __version__")
    return match.group(1)


def absolute_links(text: str, ref: str = "main") -> str:
    """The kit README with every relative link pointing at the repo on GitHub (the README
    lives in mcp_ops_kit/, so links resolve from there)."""

    def fix(match: re.Match[str]) -> str:
        target = (Path("mcp_ops_kit") / match.group(1)).as_posix()
        parts: list[str] = []
        for part in target.split("/"):
            if part == "..":
                if not parts:
                    raise ValueError(f"README link leaves the repo: {match.group(1)}")
                parts.pop()
            elif part != ".":
                parts.append(part)
        is_dir = match.group(1).endswith("/") or "." not in parts[-1].split("#")[0]
        path = "/".join(parts) + ("/" if match.group(1).endswith("/") else "")
        kind = "tree" if is_dir else "blob"
        return f"]({REPO_URL}/{kind}/{ref}/{path})"

    return RELATIVE_LINK.sub(fix, text)


def stage(dest: Path, ref: str = "main") -> Path:
    """Copy what the distribution is built from into ``dest`` and return it."""
    shutil.copy(ROOT / "packaging" / "mcp-ops-kit" / "pyproject.toml", dest)
    shutil.copy(ROOT / "LICENSE", dest)
    readme = absolute_links((KIT / "README.md").read_text(), ref)
    (dest / "README.md").write_text(readme)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "README.md")
    shutil.copytree(KIT, dest / "mcp_ops_kit", ignore=ignore)
    return dest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(ROOT / "dist" / "kit"))
    parser.add_argument("--expect-version", help="fail unless the kit is this version")
    parser.add_argument("--ref", default="main", help="git ref the README links point at")
    args = parser.parse_args(argv)

    version = kit_version()
    if args.expect_version is not None and args.expect_version != version:
        raise SystemExit(f"mcp_ops_kit is {version}, not {args.expect_version}")
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty; remove it first")
    with tempfile.TemporaryDirectory() as tmp:
        src = stage(Path(tmp), args.ref)
        cmd = [sys.executable, "-m", "build", "--outdir", str(out), str(src)]
        subprocess.run(cmd, check=True)  # noqa: S603 - our own interpreter, fixed arguments
    print(f"built mcp-ops-kit {version} into {out}")


if __name__ == "__main__":
    main()
