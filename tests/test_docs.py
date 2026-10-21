"""The controls matrix and threat model stay true to the repo (scripts/check_docs.py), on the
real documents and on copies broken one way at a time."""

from pathlib import Path

import pytest

from scripts import check_docs

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The repo with its docs/ copied (so a test can edit them) and the rest linked."""
    for entry in ROOT.iterdir():
        if entry.name not in ("docs", ".git"):
            (tmp_path / entry.name).symlink_to(entry)
    (tmp_path / "docs").mkdir()
    for name in ("hipaa-controls.md", "threat-model.md"):
        (tmp_path / "docs" / name).write_text((ROOT / "docs" / name).read_text())
    return tmp_path


def edit(repo: Path, doc: str, old: str, new: str) -> None:
    path = repo / "docs" / doc
    text = path.read_text()
    assert text.count(old) >= 1, old
    path.write_text(text.replace(old, new, 1))


def run(repo: Path) -> list[str]:
    return check_docs.check(repo, files=[])


def test_the_real_documents_pass() -> None:
    assert check_docs.check() == []


def test_the_copies_pass(repo: Path) -> None:
    assert run(repo) == []


def test_a_renamed_test_is_caught(repo: Path) -> None:
    edit(repo, "hipaa-controls.md", "::test_the_file_is_ciphertext`", "::test_gone`")
    assert any("test_gone: no such test" in p for p in run(repo))


def test_a_missing_terraform_run_is_caught(repo: Path) -> None:
    edit(repo, "threat-model.md", "::task_runs_hardened`", "::not_a_run`")
    assert any("not_a_run: no such test" in p for p in run(repo))


def test_a_missing_file_is_caught(repo: Path) -> None:
    edit(repo, "hipaa-controls.md", "`mcp_ops_kit/crypto.py`", "`mcp_ops_kit/kms.py`")
    assert any("kms.py: no such file" in p for p in run(repo))


def test_a_missing_specification_is_caught(repo: Path) -> None:
    edit(repo, "hipaa-controls.md", "| 164.312(a)(2)(ii) |", "| not-a-row |")
    assert f"{check_docs.CONTROLS}: no row for 164.312(a)(2)(ii)" in run(repo)


def test_a_claimed_control_must_cite_a_test(repo: Path) -> None:
    path = repo / "docs" / "hipaa-controls.md"
    lines = path.read_text().splitlines()
    row = next(i for i, line in enumerate(lines) if line.startswith("| 164.312(d) |"))
    cells = lines[row].split("|")
    cells[6] = " `mcp_ops_kit/oauth.py` "  # the evidence column, with every test removed
    lines[row] = "|".join(cells)
    path.write_text("\n".join(lines))
    assert f"{check_docs.CONTROLS}: 164.312(d) is Partly but cites no test" in run(repo)


def test_an_unknown_status_is_caught(repo: Path) -> None:
    edit(repo, "hipaa-controls.md", "| R | Partly |", "| R | Compliant |")
    assert any("unknown status 'Compliant'" in p for p in run(repo))


def test_every_stride_category_must_appear(repo: Path) -> None:
    edit(repo, "threat-model.md", "| R1 | Repudiation |", "| X1 | Repudiation |")
    assert f"{check_docs.THREATS}: no Repudiation threat" in run(repo)


def test_a_partly_mitigated_threat_must_say_what_is_left(repo: Path) -> None:
    path = repo / "docs" / "threat-model.md"
    lines = path.read_text().splitlines()
    row = next(i for i, line in enumerate(lines) if line.startswith("| E2 |"))
    lines[row] = lines[row].rsplit("|", 2)[0] + "| |"
    path.write_text("\n".join(lines))
    assert f"{check_docs.THREATS}: E2 is Partly: say what is left" in run(repo)


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ("This server is HIPAA compliant.", True),
        ("A HIPAA-compliant deployment in one command", True),
        ('HIPAA-ready, never "HIPAA compliant"', False),
        ("This is not a HIPAA-compliant system.", False),
    ],
)
def test_compliance_claims_are_refused(tmp_path: Path, line: str, flagged: bool) -> None:
    file = tmp_path / "README.md"
    file.write_text(f"# x\n{line}\n")
    assert bool(check_docs.check_wording([file], tmp_path)) is flagged
