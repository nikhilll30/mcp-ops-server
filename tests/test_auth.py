"""Token verification, issuing, and the per-tool guard (scopes, patient binding, rate limit)."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    DERM_PATIENT,
    DERM_PROVIDER,
    NO_REFERRAL_PATIENT,
    as_principal,
    call,
    first_open_slot,
)
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from mcp_ops_server.auth import (
    ALL_SCOPES,
    AUTH,
    StaticTokenVerifier,
    TokenEntry,
    hash_token,
    issue,
    main,
)
from mcp_ops_server.backends.sqlite import SqliteBackend
from mcp_ops_server.server import build_server

# tool -> (scope it needs, minimal valid arguments)
TOOL_SCOPES = {
    "search_providers": ("read:schedule", {}),
    "list_open_slots": ("read:schedule", {"provider_id": "PRV-001", "start_date": "2026-11-02"}),
    "book_appointment": ("write:schedule", {"patient_id": "PAT-0008", "slot_id": "SLT-00002"}),
    "cancel_appointment": (
        "write:schedule",
        {"appointment_id": "APT-00001", "patient_id": "PAT-0008"},
    ),
    "get_referral_status": ("read:referrals", {"patient_id": "PAT-0008"}),
}


FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)


def _entry(**fields: Any) -> TokenEntry:  # noqa: ANN401 - TokenEntry fields
    """A format-2 entry for this clinic's audience that has not expired."""
    return TokenEntry(**({"audience": AUTH.audience, "expires_at": FAR_FUTURE} | fields))


def _error(server: MCPServer, tool: str, args: dict) -> str:
    with pytest.raises(ToolError) as exc:
        asyncio.run(server.call_tool(tool, args))
    return str(exc.value)


# ---- verifier and issuing ---------------------------------------------------------------


def test_verifier_accepts_known_token_and_rejects_others() -> None:
    entry = _entry(client_id="c", sha256=hash_token("secret-1"), scopes=["read:schedule"])
    verifier = StaticTokenVerifier([entry])
    token = asyncio.run(verifier.verify_token("secret-1"))
    assert token is not None and token.client_id == "c" and token.scopes == ["read:schedule"]
    assert token.claims is None
    assert asyncio.run(verifier.verify_token("secret-2")) is None
    assert asyncio.run(verifier.verify_token("")) is None


def test_verifier_carries_patient_binding() -> None:
    entry = _entry(
        client_id="p", sha256=hash_token("t"), scopes=list(ALL_SCOPES), patient_id=DERM_PATIENT
    )
    token = asyncio.run(StaticTokenVerifier([entry]).verify_token("t"))
    assert token is not None and token.claims == {"patient_id": DERM_PATIENT}


def test_verifier_rejects_duplicate_hashes() -> None:
    e = _entry(client_id="c", sha256=hash_token("t"), scopes=["read:schedule"])
    with pytest.raises(ValueError, match="Duplicate"):
        StaticTokenVerifier([e, e])


@pytest.mark.parametrize(
    "bad",
    [
        {"client_id": "c", "sha256": "abc", "scopes": ["read:schedule"]},
        {"client_id": "c", "sha256": "0" * 64, "scopes": ["admin"]},
        {"client_id": "c", "sha256": "0" * 64, "scopes": [], "patient_id": "Jane"},
        {"client_id": "", "sha256": "0" * 64, "scopes": []},
    ],
)
def test_tokens_file_entries_are_validated(bad: dict) -> None:
    with pytest.raises(ValidationError):
        TokenEntry.model_validate({"audience": AUTH.audience, "expires_at": FAR_FUTURE} | bad)


def test_issue_stores_only_the_hash(tmp_path: Path) -> None:
    path = str(tmp_path / "tokens.json")
    first = issue(path, "front-desk", ["read:schedule"], None)
    second = issue(path, "portal", list(ALL_SCOPES), "PAT-0008")
    text = Path(path).read_text()
    assert first not in text and second not in text
    assert hash_token(first) in text and hash_token(second) in text
    data = json.loads(text)
    assert [t["client_id"] for t in data["tokens"]] == ["front-desk", "portal"]
    verifier = StaticTokenVerifier.from_file(path)
    assert asyncio.run(verifier.verify_token(second)).claims == {"patient_id": "PAT-0008"}


def test_issue_cli_prints_a_working_token(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = str(tmp_path / "tokens.json")
    main(["issue", "--file", path, "--client", "dev", "--scope", "read:schedule"])
    token = capsys.readouterr().out.strip()
    assert len(token) >= 40
    assert asyncio.run(StaticTokenVerifier.from_file(path).verify_token(token)) is not None


# ---- guard: authentication and scopes ------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(TOOL_SCOPES))
def test_every_tool_refuses_unauthenticated_calls(server: MCPServer, tool: str) -> None:
    reset = auth_context_var.set(None)
    try:
        assert "Unauthenticated" in _error(server, tool, TOOL_SCOPES[tool][1])
    finally:
        auth_context_var.reset(reset)


@pytest.mark.parametrize("tool", sorted(TOOL_SCOPES))
def test_every_tool_requires_its_scope(server: MCPServer, tool: str) -> None:
    needed, args = TOOL_SCOPES[tool]
    others = [s for s in ALL_SCOPES if s != needed]
    with as_principal(scopes=others):
        assert f"lacks the {needed} scope" in _error(server, tool, args)


def test_health_needs_no_scope(server: MCPServer) -> None:
    with as_principal(scopes=[]):
        assert call(server, "health")["status"] == "ok"


def test_rejected_call_has_no_side_effect(server: MCPServer) -> None:
    slot_id = first_open_slot(server, "PRV-001", NO_REFERRAL_PATIENT)
    with as_principal(scopes=["read:schedule"]):
        _error(server, "book_appointment", {"patient_id": NO_REFERRAL_PATIENT, "slot_id": slot_id})
    assert first_open_slot(server, "PRV-001", NO_REFERRAL_PATIENT) == slot_id


# ---- guard: patient binding ----------------------------------------------------------------


def test_patient_bound_token_can_act_for_its_patient(server: MCPServer) -> None:
    slot_id = first_open_slot(server, DERM_PROVIDER, DERM_PATIENT)
    with as_principal(client_id="portal", patient_id=DERM_PATIENT):
        refs = call(server, "get_referral_status", patient_id=DERM_PATIENT)
        approved = next(r for r in refs["referrals"] if r["status"] == "approved")
        appt = call(
            server,
            "book_appointment",
            patient_id=DERM_PATIENT,
            slot_id=slot_id,
            referral_id=approved["referral_id"],
        )
        call(
            server,
            "cancel_appointment",
            appointment_id=appt["appointment_id"],
            patient_id=DERM_PATIENT,
        )


@pytest.mark.parametrize("tool", ["book_appointment", "cancel_appointment", "get_referral_status"])
def test_patient_bound_token_cannot_act_for_others(server: MCPServer, tool: str) -> None:
    with as_principal(client_id="portal", patient_id=DERM_PATIENT):
        msg = _error(server, tool, TOOL_SCOPES[tool][1])  # these args name PAT-0008
    assert f"may only act for patient {DERM_PATIENT}" in msg


def test_patient_bound_token_can_still_browse_schedule(server: MCPServer) -> None:
    with as_principal(client_id="portal", patient_id=DERM_PATIENT):
        assert call(server, "search_providers", specialty="dermatology")["count"] == 2


# ---- guard: rate limit ---------------------------------------------------------------------


def test_rate_limit_is_per_client() -> None:
    server = build_server(SqliteBackend(), rate_limit_per_minute=3)
    with as_principal(client_id="busy"):
        for _ in range(3):
            call(server, "search_providers")
        assert "Rate limit exceeded for client busy" in _error(server, "search_providers", {})
    with as_principal(client_id="quiet"):
        call(server, "search_providers")


def test_rate_limit_counts_rejected_calls() -> None:
    """Probing with a token that lacks a scope still spends that client's budget."""
    server = build_server(SqliteBackend(), rate_limit_per_minute=2)
    with as_principal(client_id="prober", scopes=["read:schedule"]):
        for _ in range(2):
            _error(server, "get_referral_status", {"patient_id": "PAT-0008"})
        assert "Rate limit exceeded" in _error(server, "search_providers", {})


def test_invalid_arguments_still_spend_the_budget() -> None:
    """Rate limiting runs before argument validation (Codex review, Part 3)."""
    server = build_server(SqliteBackend(), rate_limit_per_minute=2)
    with as_principal(client_id="fuzzer"):
        for _ in range(2):
            assert "validation error" in _error(server, "search_providers", {"limit": 999})
        assert "Rate limit exceeded" in _error(server, "search_providers", {})


def test_health_counts_toward_the_rate_limit() -> None:
    server = build_server(SqliteBackend(), rate_limit_per_minute=1)
    with as_principal(client_id="pinger"):
        call(server, "health")
        assert "Rate limit exceeded" in _error(server, "health", {})


def test_each_token_has_its_own_subject() -> None:
    """Distinct subjects keep tokens that share a client_id in separate HTTP sessions."""
    entries = [
        _entry(client_id="portal", sha256=hash_token(t), scopes=list(ALL_SCOPES))
        for t in ("a", "b")
    ]
    verifier = StaticTokenVerifier(entries)
    a = asyncio.run(verifier.verify_token("a"))
    b = asyncio.run(verifier.verify_token("b"))
    assert a.client_id == b.client_id and a.subject != b.subject


def test_issue_cli_explains_an_unwritable_tokens_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """In Docker the image's non-root user often cannot write the mounted folder; say how
    to fix it instead of printing a traceback. (The CLI is the kit's, configured by the
    clinic, so the write is stubbed where the kit makes it.)"""
    import mcp_ops_kit.auth as kit_auth

    def denied(*args: object, **kwargs: object) -> str:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(kit_auth, "issue", denied)
    with pytest.raises(SystemExit) as exc:
        main(["issue", "--file", "/data/tokens.json", "--client", "x", "--scope", "read:schedule"])
    assert "Cannot write /data/tokens.json: Permission denied" in str(exc.value)
    assert '--user "$(id -u):$(id -g)"' in str(exc.value)
