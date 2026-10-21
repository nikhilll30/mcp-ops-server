"""The Terraform blueprint's own rules (scripts/check_infra.py), on the real infra/aws and on
copies broken one way at a time. Terraform itself runs in CI's terraform job; these need
only Python, so they run in every test job."""

import json
import shutil
from pathlib import Path

import pytest

from scripts import check_infra

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A copy of what the checker reads: infra/aws/*.tf, the catalog, .env.example, Dockerfile."""
    infra = tmp_path / "infra" / "aws"
    infra.mkdir(parents=True)
    for p in (ROOT / "infra" / "aws").glob("*.tf"):
        shutil.copy(p, infra / p.name)
    shutil.copy(ROOT / "infra" / "aws" / "hipaa-services.json", infra)
    for name in (".env.example", "Dockerfile"):
        shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path


def run(repo: Path) -> list[str]:
    return check_infra.check(repo / "infra" / "aws", repo)


def append(repo: Path, file: str, text: str) -> None:
    path = repo / "infra" / "aws" / file
    path.write_text(path.read_text() + "\n" + text + "\n")


def test_the_real_blueprint_passes() -> None:
    assert check_infra.check() == []


def test_the_copy_passes(repo: Path) -> None:
    assert run(repo) == []


def test_every_listed_type_is_used() -> None:
    """No stale entries: the catalog lists exactly the types the blueprint uses."""
    used = set()
    for p in check_infra.tf_files(check_infra.INFRA):
        used |= {m.group(2) for m in check_infra.BLOCK.finditer(p.read_text())}
    catalog = json.loads((check_infra.INFRA / "hipaa-services.json").read_text())
    assert set(catalog["types"]) == used


def test_an_unlisted_type_fails(repo: Path) -> None:
    append(repo, "main.tf", 'resource "aws_sqs_queue" "jobs" {\n  name = "jobs"\n}')
    problems = run(repo)
    assert len(problems) == 1
    assert "aws_sqs_queue.jobs: not in hipaa-services.json" in problems[0]
    assert "main.tf:" in problems[0]


@pytest.mark.parametrize(
    "type_",
    ["aws_internet_gateway", "aws_nat_gateway", "aws_eip", "aws_secretsmanager_secret_version"],
)
def test_a_forbidden_type_fails(repo: Path, type_: str) -> None:
    append(repo, "network.tf", f'resource "{type_}" "x" {{\n}}')
    problems = run(repo)
    assert len(problems) == 1 and f"{type_}.x: forbidden" in problems[0]


def test_a_forbidden_type_fails_even_if_listed(repo: Path) -> None:
    catalog_path = repo / "infra" / "aws" / "hipaa-services.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["types"]["aws_internet_gateway"] = "Amazon VPC"
    catalog_path.write_text(json.dumps(catalog))
    append(repo, "network.tf", 'resource "aws_internet_gateway" "x" {\n}')
    assert any("aws_internet_gateway.x: forbidden" in p for p in run(repo))


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "::/0"])
def test_an_open_cidr_fails(repo: Path, cidr: str) -> None:
    append(
        repo,
        "security_groups.tf",
        f'resource "aws_vpc_security_group_ingress_rule" "open" {{\n  cidr_ipv4 = "{cidr}"\n}}',
    )
    problems = run(repo)
    assert len(problems) == 1 and "opens the blueprint to everyone" in problems[0]


def test_an_undocumented_setting_fails(repo: Path) -> None:
    path = repo / "infra" / "aws" / "main.tf"
    text = path.read_text()
    assert "MCP_OPS_NOW " in text
    path.write_text(text.replace("MCP_OPS_NOW ", "MCP_OPS_CLOCK ", 1))
    assert run(repo) == ["MCP_OPS_CLOCK is passed to the task but not documented in .env.example"]


def test_a_uid_that_is_not_the_images_fails(repo: Path) -> None:
    path = repo / "Dockerfile"
    path.write_text(path.read_text().replace("--uid 10001", "--uid 10002"))
    assert run(repo) == ["infra/aws runs as uid 10001, the image's user is 10002"]


def test_a_service_with_no_status_fails(repo: Path) -> None:
    catalog_path = repo / "infra" / "aws" / "hipaa-services.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["services"]["AWS IAM"] = {"status": "no-phi"}  # no reason
    catalog["services"]["Amazon S3"]["status"] = "probably"
    catalog_path.write_text(json.dumps(catalog))
    problems = run(repo)
    assert "hipaa-services.json: AWS IAM: a no-phi service needs a reason" in problems
    assert "hipaa-services.json: Amazon S3: status must be eligible or no-phi" in problems


def test_main_exit_codes(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert check_infra.main() == 0
    assert capsys.readouterr().out.startswith("ok: every type is listed.")
    monkeypatch.setattr(check_infra, "check", lambda: ["a problem"])
    assert check_infra.main() == 1
    assert capsys.readouterr().out == "a problem\n"
