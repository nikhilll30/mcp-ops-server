"""Checks the Terraform blueprint (infra/aws) against rules no Terraform linter knows.

    python scripts/check_infra.py            # exit 1 with one line per problem

- Every resource and data source type is listed in ``infra/aws/hipaa-services.json``,
  mapped to a service that is HIPAA-eligible or stores no PHI. A new type fails until
  someone looks it up and adds it. Forbidden types (internet gateways, NAT, public IPs,
  IAM users, secret values in state) always fail.
- No security rule or variable default opens ``0.0.0.0/0`` or ``::/0``.
- Every ``MCP_OPS_*`` setting the task definition passes is documented in ``.env.example``,
  so the blueprint cannot drift to a name the server does not read.
- The uid the blueprint runs as is the image's own (Dockerfile ``useradd --uid``).

Plain text parsing, no HCL library: ``terraform fmt`` (checked in CI first) fixes the layout
these patterns rely on.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra" / "aws"

BLOCK = re.compile(r'^(resource|data)\s+"([a-z0-9_]+)"\s+"([A-Za-z0-9_-]+)"', re.M)
SETTING = re.compile(r"\bMCP_OPS_[A-Z0-9_]+\b")
OPEN_CIDR = re.compile(r'"(0\.0\.0\.0/0|::/0)"')
UID_TF = re.compile(r"^\s*app_uid\s*=\s*(\d+)", re.M)
UID_DOCKER = re.compile(r"useradd\s+--uid\s+(\d+)")


def tf_files(infra: Path) -> list[Path]:
    """The blueprint's own .tf files (not tests, not the .terraform/ provider cache)."""
    return sorted(infra.glob("*.tf"))


def check(infra: Path = INFRA, root: Path = ROOT) -> list[str]:
    """Every problem found, one line each; empty means the blueprint passes."""
    problems: list[str] = []
    catalog = json.loads((infra / "hipaa-services.json").read_text())
    services: dict = catalog["services"]
    types: dict = catalog["types"]
    forbidden: dict = catalog["forbidden"]

    for name, svc in services.items():
        if svc.get("status") not in ("eligible", "no-phi"):
            problems.append(f"hipaa-services.json: {name}: status must be eligible or no-phi")
        if svc.get("status") == "no-phi" and not svc.get("reason"):
            problems.append(f"hipaa-services.json: {name}: a no-phi service needs a reason")
    for type_, svc in types.items():
        if svc not in services:
            problems.append(f"hipaa-services.json: {type_} maps to unknown service {svc!r}")

    files = tf_files(infra)
    if not files:
        problems.append(f"{infra}: no .tf files")
    settings: set[str] = set()
    for path in files:
        text = path.read_text()
        rel = path.relative_to(root) if path.is_relative_to(root) else path
        for match in BLOCK.finditer(text):
            kind, type_, name = match.groups()
            line = text.count("\n", 0, match.start()) + 1
            where = f"{rel}:{line} {kind} {type_}.{name}"
            if type_ in forbidden:
                problems.append(f"{where}: forbidden ({forbidden[type_]})")
            elif type_ not in types:
                problems.append(
                    f"{where}: not in hipaa-services.json; check the service is "
                    "HIPAA-eligible (or stores no PHI) and add it"
                )
        for match in OPEN_CIDR.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            problems.append(f"{rel}:{line}: {match.group(1)} opens the blueprint to everyone")
        settings |= set(SETTING.findall(text))

    documented = set(SETTING.findall((root / ".env.example").read_text()))
    for name in sorted(settings - documented):
        problems.append(f"{name} is passed to the task but not documented in .env.example")

    uid_tf = UID_TF.search("\n".join(p.read_text() for p in files))
    uid_docker = UID_DOCKER.search((root / "Dockerfile").read_text())
    if not uid_tf or not uid_docker:
        problems.append("could not find app_uid in infra/aws or useradd --uid in the Dockerfile")
    elif uid_tf.group(1) != uid_docker.group(1):
        problems.append(
            f"infra/aws runs as uid {uid_tf.group(1)}, the image's user is {uid_docker.group(1)}"
        )
    return problems


def evidence_summary(infra: Path = INFRA) -> str:
    """Which services the blueprint uses, by how their eligibility was confirmed."""
    catalog = json.loads((infra / "hipaa-services.json").read_text())
    by: dict[str, list[str]] = {}
    for name, svc in catalog["services"].items():
        by.setdefault(svc.get("evidence", svc["status"]), []).append(name)
    return "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(by.items()))


def main() -> int:
    problems = check()
    for p in problems:
        print(p)
    if problems:
        return 1
    print(f"ok: every type is listed. {evidence_summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
