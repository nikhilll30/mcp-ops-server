"""Checks the assurance documents against the repo, so they cannot quietly go stale.

    python scripts/check_docs.py            # exit 1 with one line per problem

- ``docs/hipaa-controls.md`` has one row for every standard and implementation
  specification of 45 CFR 164.312, each with a known status. A row marked ``Addressed`` or
  ``Partly`` cites at least one test, and every row says what is left to the adopter.
- ``docs/threat-model.md`` covers every STRIDE category. A threat marked ``Mitigated`` or
  ``Partly`` cites at least one test, and one marked ``Partly`` or ``Open`` says what is left.
- Every reference cited in either document's evidence column exists: a file, a test
  (``file.py::test_name``, a ``def`` in that file) or a Terraform test run
  (``file.tftest.hcl::name``, a ``run "name"`` block). Renaming or deleting a test that a
  control cites fails here until the document is updated.
- No text file in the repo calls anything "HIPAA compliant", except to say it is not
  (a line that also says never, not or no). This repo is HIPAA-ready, never compliant.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTROLS = Path("docs/hipaa-controls.md")
THREATS = Path("docs/threat-model.md")

# 45 CFR 164.312: each standard and its implementation specifications.
SPECS = (
    "164.312(a)(1)",
    "164.312(a)(2)(i)",
    "164.312(a)(2)(ii)",
    "164.312(a)(2)(iii)",
    "164.312(a)(2)(iv)",
    "164.312(b)",
    "164.312(c)(1)",
    "164.312(c)(2)",
    "164.312(d)",
    "164.312(e)(1)",
    "164.312(e)(2)(i)",
    "164.312(e)(2)(ii)",
)
CONTROL_STATUS = {"Addressed", "Partly", "Adopter"}
THREAT_STATUS = {"Mitigated", "Partly", "Open"}
STRIDE = (
    "Spoofing",
    "Tampering",
    "Repudiation",
    "Information disclosure",
    "Denial of service",
    "Elevation of privilege",
)
REF = re.compile(r"`([A-Za-z0-9_./-]+(?:::[A-Za-z0-9_]+)?)`")
COMPLIANT = re.compile(r"hipaa[- ]compliant", re.I)
NEGATION = re.compile(r"\b(never|not|no)\b", re.I)
TEXT_SUFFIXES = {".md", ".py", ".txt", ".toml", ".yml", ".yaml", ".tf", ".hcl", ".json", ".sh"}


def table_rows(text: str, first_cell: re.Pattern[str]) -> list[list[str]]:
    """The cells of each markdown table row whose first cell matches ``first_cell``."""
    rows = []
    for line in text.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if first_cell.fullmatch(cells[0]):
                rows.append(cells)
    return rows


def check_ref(ref: str, root: Path) -> str | None:
    """Why ``ref`` does not resolve, or None if it does."""
    path, _, name = ref.partition("::")
    file = root / path
    if not file.is_file():
        return f"{ref}: no such file"
    if not name:
        return None
    text = file.read_text()
    if file.suffix == ".py":
        found = re.search(rf"^(async )?def {re.escape(name)}\(", text, re.M)
    elif file.name.endswith(".tftest.hcl"):
        found = re.search(rf'^run "{re.escape(name)}"', text, re.M)
    else:
        return f"{ref}: can only cite tests in .py or .tftest.hcl files"
    return None if found else f"{ref}: no such test in {path}"


def _is_test(ref: str) -> bool:
    return "::" in ref and (ref.startswith("tests/") or ".tftest.hcl::" in ref)


def check_controls(root: Path) -> list[str]:
    problems: list[str] = []
    text = (root / CONTROLS).read_text()
    rows = table_rows(text, re.compile(r"164\.312\S+"))
    seen = [r[0] for r in rows]
    problems += [f"{CONTROLS}: no row for {s}" for s in SPECS if s not in seen]
    problems += [f"{CONTROLS}: unknown or repeated section {s}" for s in seen if s not in SPECS]
    if len(set(seen)) != len(seen):
        problems.append(f"{CONTROLS}: a section has more than one row")
    for cells in rows:
        if len(cells) != 7:
            problems.append(f"{CONTROLS}: {cells[0]} has {len(cells)} columns, not 7")
            continue
        section, _, req, status, _, evidence, adopter = cells
        refs = REF.findall(evidence)
        if req not in ("R", "A"):
            problems.append(f"{CONTROLS}: {section}: R/A is {req!r}")
        if status not in CONTROL_STATUS:
            problems.append(f"{CONTROLS}: {section}: unknown status {status!r}")
        if status in ("Addressed", "Partly") and not any(_is_test(r) for r in refs):
            problems.append(f"{CONTROLS}: {section} is {status} but cites no test")
        if not adopter:
            problems.append(f"{CONTROLS}: {section}: say what is left to the adopter")
        problems += [f"{CONTROLS}: {p}" for r in refs if (p := check_ref(r, root))]
    return problems


def check_threats(root: Path) -> list[str]:
    problems: list[str] = []
    text = (root / THREATS).read_text()
    rows = table_rows(text, re.compile(r"[STRIDE]\d+"))
    ids = [r[0] for r in rows]
    if len(set(ids)) != len(ids):
        problems.append(f"{THREATS}: a threat ID is used twice")
    categories = set()
    for cells in rows:
        if len(cells) != 8:
            problems.append(f"{THREATS}: {cells[0]} has {len(cells)} columns, not 8")
            continue
        tid, stride, _, _, _, evidence, status, residual = cells
        categories.add(stride)
        if stride not in STRIDE or tid[0] != stride[0]:
            problems.append(f"{THREATS}: {tid}: category {stride!r} does not match its ID")
        if status not in THREAT_STATUS:
            problems.append(f"{THREATS}: {tid}: unknown status {status!r}")
        refs = REF.findall(evidence)
        if status in ("Mitigated", "Partly") and not any(_is_test(r) for r in refs):
            problems.append(f"{THREATS}: {tid} is {status} but cites no test")
        if status in ("Partly", "Open") and not residual:
            problems.append(f"{THREATS}: {tid} is {status}: say what is left")
        problems += [f"{THREATS}: {p}" for r in refs if (p := check_ref(r, root))]
    problems += [f"{THREATS}: no {c} threat" for c in STRIDE if c not in categories]
    return problems


def tracked_text_files(root: Path) -> list[Path]:
    out = subprocess.run(  # noqa: S603 - fixed arguments
        ["git", "ls-files", "-z"],  # noqa: S607 - git from PATH, as every contributor has it
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [root / p for p in out.split("\0") if p and Path(p).suffix in TEXT_SUFFIXES]


def check_wording(files: list[Path], root: Path) -> list[str]:
    problems = []
    for file in files:
        if not file.is_file():
            continue  # deleted in the working tree, not yet in git
        for n, line in enumerate(file.read_text(errors="replace").splitlines(), 1):
            if COMPLIANT.search(line) and not NEGATION.search(line):
                problems.append(f"{file.relative_to(root)}:{n}: claims HIPAA compliance")
    return problems


def check(root: Path = ROOT, files: list[Path] | None = None) -> list[str]:
    """Every problem found, one line each; empty means the documents pass."""
    files = tracked_text_files(root) if files is None else files
    return check_controls(root) + check_threats(root) + check_wording(files, root)


def main() -> None:
    problems = check()
    for p in problems:
        print(p)
    if problems:
        sys.exit(1)
    print(f"ok: {CONTROLS} ({len(SPECS)} specifications), {THREATS}, wording")


if __name__ == "__main__":
    main()
