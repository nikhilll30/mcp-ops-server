"""Checks a built mcp-ops-kit distribution before it can reach PyPI.

    python scripts/check_kit_dist.py check DIST_DIR   # the files in the wheel and sdist
    python -I scripts/check_kit_dist.py smoke         # run inside a venv with only the wheel

``check`` (exit 1 with one line per problem):
- exactly one wheel and one sdist, both for ``mcp_ops_kit.__version__``;
- the wheel holds every module of ``mcp_ops_kit/`` and nothing else: no domain package
  (``mcp_ops_server``, ``salon_ops``), no tests, evals data, scripts or infra;
- the sdist holds only the kit, its README, LICENSE and pyproject.toml;
- the metadata names the project, the version and the dependencies of
  ``packaging/mcp-ops-kit/pyproject.toml``.

``smoke`` uses only the installed wheel (``-I`` keeps this checkout off ``sys.path``): every
module imports from site-packages, a domain's token round trip works (issue, verify, revoke,
refuse), ``python -m mcp_ops_kit.crypto new-key`` works, a server builds with its ``health``
tool, and no domain package came with it.
"""

import argparse
import asyncio
import importlib
import importlib.util
import json
import pkgutil
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "mcp_ops_kit"
PYPROJECT = ROOT / "packaging" / "mcp-ops-kit" / "pyproject.toml"
VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.M)
DOMAINS = ("mcp_ops_server", "salon_ops", "evals", "scripts", "tests", "infra")


def kit_files(kit: Path = KIT) -> set[str]:
    """What the wheel must hold: every file of the package except its README and caches."""
    return {
        f"mcp_ops_kit/{p.relative_to(kit).as_posix()}"
        for p in kit.rglob("*")
        if p.is_file() and p.name != "README.md" and "__pycache__" not in p.parts
    }


def _requirement(text: str, extra: str | None = None) -> str:
    """One requirement in a canonical spelling, so pyproject's ``mcp>=2.3,<3`` equals the
    metadata's ``mcp<3,>=2.3``. Enough for the plain requirements this project writes."""
    req, _, marker = text.partition(";")
    name, specs = re.fullmatch(r"\s*([A-Za-z0-9_.\-\[\]]+)\s*(.*?)\s*", req).groups()
    canon = name.lower().replace("_", "-") + ",".join(sorted(re.sub(r"\s", "", specs).split(",")))
    marker = re.sub(r"\s", "", marker).replace('"', "'")
    if extra is not None:
        marker = f"extra=='{extra}'"
    return f"{canon};{marker}" if marker else canon


def check(dist: Path, kit: Path = KIT, pyproject: Path = PYPROJECT) -> list[str]:
    """Every problem with the distribution in ``dist``; empty means it may be published."""
    problems: list[str] = []
    match = VERSION.search((kit / "__init__.py").read_text())
    version = match.group(1) if match else "?"
    project = tomllib.loads(pyproject.read_text())["project"]
    wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
    others = sorted(p.name for p in dist.iterdir() if p not in {*wheels, *sdists})
    if len(wheels) != 1 or len(sdists) != 1 or others:
        names = sorted(p.name for p in dist.iterdir())
        return [f"expected one wheel and one sdist, found {names}"]
    wheel, sdist = wheels[0], sdists[0]
    if wheel.name != f"mcp_ops_kit-{version}-py3-none-any.whl":
        problems.append(f"wheel {wheel.name} is not mcp_ops_kit {version}, pure Python")
    if sdist.name != f"mcp_ops_kit-{version}.tar.gz":
        problems.append(f"sdist {sdist.name} is not mcp_ops_kit {version}")

    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        info = f"mcp_ops_kit-{version}.dist-info/"
        meta_name = info + "METADATA"
        meta = Parser().parsestr(zf.read(meta_name).decode()) if meta_name in names else None
    package = {n for n in names if not n.startswith(info)}
    expected = kit_files(kit)
    problems += [f"wheel is missing {n}" for n in sorted(expected - package)]
    problems += [f"wheel must not contain {n}" for n in sorted(package - expected)]
    if meta is None:
        problems.append(f"wheel has no {meta_name}")
    else:
        if meta["Name"] != "mcp-ops-kit" or meta["Version"] != version:
            problems.append(f"metadata is {meta['Name']} {meta['Version']}, not mcp-ops-kit")
        declared = {_requirement(r) for r in project["dependencies"]}
        for extra, reqs in project.get("optional-dependencies", {}).items():
            declared |= {_requirement(r, extra) for r in reqs}
        got = {_requirement(r) for r in meta.get_all("Requires-Dist") or []}
        if got != declared:
            problems.append(
                f"Requires-Dist {sorted(got)} differs from pyproject {sorted(declared)}"
            )

    with tarfile.open(sdist) as tf:
        top = f"mcp_ops_kit-{version}/"
        members = {m.name.removeprefix(top) for m in tf.getmembers() if m.isfile()}
    allowed = expected | {"README.md", "LICENSE", "pyproject.toml", "PKG-INFO"}
    problems += [f"sdist must not contain {n}" for n in sorted(members - allowed)]
    problems += [f"sdist is missing {n}" for n in sorted(allowed - members)]
    for name in sorted(package | members):
        if name.split("/")[0] in DOMAINS:
            problems.append(f"distribution contains domain or repo-only file {name}")
    return problems


def smoke() -> None:
    """Exercise the installed kit; raises on the first failure."""
    import mcp_ops_kit
    from mcp_ops_kit import auth
    from mcp_ops_kit.server import build_server

    location = Path(mcp_ops_kit.__file__).resolve()
    if "site-packages" not in location.parts:
        raise SystemExit(f"mcp_ops_kit was imported from {location}, not the installed wheel")
    for mod in pkgutil.walk_packages(mcp_ops_kit.__path__, "mcp_ops_kit."):
        importlib.import_module(mod.name)
    for domain in ("mcp_ops_server", "salon_ops"):
        if importlib.util.find_spec(domain) is not None:
            raise SystemExit(f"{domain} is importable: the wheel must carry only the kit")

    config = auth.AuthConfig(
        scopes=("read:things",),
        subject_claim="owner_id",
        subject_pattern=r"OWN-[0-9]{4}",
        subject_label="owner",
        cli_prog="python -m example.auth",
        audience="example",
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "tokens.json")
        token = auth.issue(config, path, "smoke", ["read:things"], "OWN-0001")
        verifier = config.verifier.from_file(path)
        accepted = asyncio.run(verifier.verify_token(token or ""))
        if accepted is None or accepted.client_id != "smoke":
            raise SystemExit("an issued token was not accepted")
        token_id = auth.load_tokens(config, path)[0].token_id
        auth.revoke(config, path, token_id=token_id)
        if asyncio.run(verifier.verify_token(token or "")) is not None:
            raise SystemExit("a revoked token was still accepted")

    key = subprocess.run(  # noqa: S603 - our own interpreter, fixed arguments
        [sys.executable, "-I", "-m", "mcp_ops_kit.crypto", "new-key"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if len(key) < 40:
        raise SystemExit("crypto new-key printed no key")

    server = build_server("example", mcp_ops_kit.__version__, config, lambda s, g: None)
    tools = [t.name for t in asyncio.run(server.list_tools())]
    if tools != ["health"]:
        raise SystemExit(f"a server with no domain tools lists {tools}")
    print(json.dumps({"ok": True, "version": mcp_ops_kit.__version__, "from": str(location)}))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").add_argument("dist")
    sub.add_parser("smoke")
    args = parser.parse_args(argv)
    if args.cmd == "smoke":
        smoke()
        return
    problems = check(Path(args.dist))
    for p in problems:
        print(p)
    if problems:
        raise SystemExit(1)
    print(f"ok: {args.dist}")


if __name__ == "__main__":
    main()
