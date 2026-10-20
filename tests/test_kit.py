"""mcp-ops-kit on its own: domain-free, and strict about tokens from another domain.

The kit's behaviour inside a server is covered by both domains' suites (the clinic's tests
and eval gate, the salon's); these tests pin what only the kit decides.
"""

import ast
import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import as_principal
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from mcp_ops_kit import auth
from mcp_ops_kit.evals.openai_agent import OpenAIAgent
from mcp_ops_kit.limits import RateLimiter
from mcp_ops_kit.server import ServerSettings, make_clock
from mcp_ops_server.auth import AUTH as CLINIC
from salon_ops import seed as salon_seed
from salon_ops.auth import AUTH as SALON
from salon_ops.config import Settings as SalonSettings

KIT = Path(__file__).parent.parent / "mcp_ops_kit"


def test_the_kit_never_imports_a_domain() -> None:
    """Extracted, not just moved: nothing in mcp_ops_kit may depend on a domain package."""
    domains = {"mcp_ops_server", "salon_ops", "evals", "scripts"}
    offenders = []
    for path in KIT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] in domains]
    assert offenders == []


def test_one_domains_tokens_file_does_not_load_in_another(tmp_path: Path) -> None:
    """A clinic token bound to a patient must not become an unbound salon token (its
    ``patient_id`` key would otherwise be ignored), and clinic scopes mean nothing here."""
    path = str(tmp_path / "tokens.json")
    auth.issue(CLINIC, path, "portal", ["read:schedule"], "PAT-0001")
    with pytest.raises(ValidationError):
        SALON.verifier.from_file(path)
    bound_only = {"client_id": "x", "sha256": "0" * 64, "scopes": [], "patient_id": "PAT-0001"}
    with pytest.raises(ValidationError, match="Extra inputs"):
        SALON.token_entry.model_validate(bound_only)


@pytest.mark.parametrize(
    "bad",
    [
        {"client_id": "c", "sha256": "0" * 64, "scopes": ["read:schedule"]},  # clinic scope
        {"client_id": "c", "sha256": "0" * 64, "scopes": [], "customer_id": "PAT-0001"},
        {"client_id": "c", "sha256": "0" * 64, "scopes": [], "subject": "CUS-0001"},
        {"client_id": "c", "sha256": "0" * 64, "scopes": [], "customer_id": "CUS-0001\n"},
    ],
)
def test_salon_token_entries_are_validated(bad: dict) -> None:
    with pytest.raises(ValidationError):
        SALON.token_entry.model_validate(bad)


def test_tokens_file_keeps_the_domains_claim_name(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    token = auth.issue(SALON, str(path), "app", ["read:catalog"], "CUS-0005")
    (entry,) = json.loads(path.read_text())["tokens"]
    assert entry["customer_id"] == "CUS-0005" and "subject" not in entry
    verified = asyncio.run(SALON.verifier.from_file(str(path)).verify_token(token))
    assert verified is not None and verified.claims == {"customer_id": "CUS-0005"}


@pytest.mark.parametrize(
    "content", ['{"tokens": {}}', "[]", '{"tokens": [], "extra": 1}', "not json"]
)
def test_malformed_tokens_files_are_refused(tmp_path: Path, content: str) -> None:
    path = tmp_path / "tokens.json"
    path.write_text(content)
    with pytest.raises(ValueError):  # noqa: PT011 - json and shape errors are both ValueErrors
        SALON.verifier.from_file(str(path))


def test_auth_config_rejects_ambiguous_setups() -> None:
    with pytest.raises(ValueError, match="unique"):
        auth.AuthConfig(("a", "a"), "customer_id", "C-[0-9]", "customer", "x")
    with pytest.raises(ValueError, match="collides"):
        auth.AuthConfig(("a",), "scopes", "C-[0-9]", "customer", "x")


def test_guard_refuses_a_scope_the_domain_never_declared() -> None:
    """A typo in a tool's scope name is a programming error, not a quiet 'forbidden'."""
    guard = auth.Guard(SALON, RateLimiter(10))
    with as_principal("x", list(SALON.scopes)), pytest.raises(ValueError, match="unknown scope"):
        guard.check("write:booking")
    with as_principal("x", ["read:catalog"]), pytest.raises(ToolError, match="lacks"):
        guard.check("write:bookings")


def test_settings_from_env_use_the_domains_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SALON_OPS_PORT", "9100")
    monkeypatch.setenv("SALON_OPS_TOKENS_FILE", "t.json")
    monkeypatch.setenv("SALON_OPS_RATE_LIMIT_PER_MIN", "7")
    monkeypatch.setenv("SALON_OPS_NOW", "2026-11-05T12:00:00")
    monkeypatch.setenv("MCP_OPS_PORT", "1")  # the clinic's variables do not leak in
    s = SalonSettings.from_env()
    assert (s.port, s.tokens_file, s.rate_limit_per_minute) == (9100, "t.json", 7)
    assert s.now == datetime(2026, 11, 5, 12, 0) and s.db_path == ":memory:"
    monkeypatch.setenv("SALON_OPS_NOW", "system")
    assert SalonSettings.from_env().now is None
    monkeypatch.delenv("SALON_OPS_NOW")
    assert SalonSettings.from_env().now == salon_seed.SALON_NOW
    assert ServerSettings().port == 8000 and SalonSettings().port == 8100


def test_make_clock_is_pinned_or_domain_local() -> None:
    pinned = datetime(2026, 11, 2, 18, 0)
    assert make_clock(pinned, UTC)() == pinned
    plus_one = timezone(timedelta(hours=1))
    now = make_clock(None, plus_one)()
    expected = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    assert now.tzinfo is None and abs((now - expected).total_seconds()) < 60


def test_live_agent_needs_a_prompt_that_states_the_time() -> None:
    with pytest.raises(ValueError, match="now"):
        OpenAIAgent("m", "You book things.", salon_seed.SALON_NOW, client=object())


def test_overlapping_scope_names_are_the_documented_limit(tmp_path: Path) -> None:
    """Codex review, PR #18: a tokens file names no domain. With shared scope names, a token
    *bound* in one domain is still refused by the other (its claim is unknown there), but an
    unbound token loads in both. The kit README states this limit; this test pins it."""
    a = auth.AuthConfig(("read:x",), "member_id", r"M-[0-9]{2}", "member", "a")
    b = auth.AuthConfig(("read:x",), "guest_id", r"G-[0-9]{2}", "guest", "b")
    bound, unbound = str(tmp_path / "bound.json"), str(tmp_path / "unbound.json")
    auth.issue(a, bound, "app", ["read:x"], "M-01")
    with pytest.raises(ValidationError, match="Extra inputs"):
        b.verifier.from_file(bound)
    token = auth.issue(a, unbound, "desk", ["read:x"], None)
    verified = asyncio.run(b.verifier.from_file(unbound).verify_token(token))
    assert verified is not None and verified.claims is None


@pytest.mark.parametrize("var", ["PORT", "RATE_LIMIT_PER_MIN", "CALL_TIMEOUT_S", "MAX_SESSIONS"])
def test_an_empty_numeric_setting_is_an_error_not_the_default(
    monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    """Codex review, PR #18: before the kit, an empty MCP_OPS_PORT failed to parse; the kit
    briefly treated it as unset and served on the default port."""
    from mcp_ops_server.config import Settings as ClinicSettings

    monkeypatch.setenv(f"MCP_OPS_{var}", "")
    with pytest.raises(ValueError, match=f"MCP_OPS_{var}=''"):
        ClinicSettings.from_env()
    monkeypatch.setenv(f"MCP_OPS_{var}", "12x")
    with pytest.raises(ValueError, match=f"MCP_OPS_{var}='12x'"):
        ClinicSettings.from_env()
