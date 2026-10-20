"""End-to-end checks: launch `python -m mcp_ops_server` and drive it over Streamable HTTP.

Catches failures the in-process tests cannot: a broken entry point, bad transport
arguments, env-var configuration that does not reach the server, or results and errors
that do not survive serialization to a real client.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx2
import pytest
from conftest import DERM_PATIENT, DERM_REFERRAL, WEEK_ONE, safeguard_env, seeded_busy_starts
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_ops_kit import audit
from mcp_ops_kit.crypto import DataKey
from mcp_ops_server import __version__
from mcp_ops_server.auth import ALL_SCOPES, issue

MAX_BODY_BYTES = 16 * 1024


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            err = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(f"server exited with code {proc.returncode}:\n{err[-2000:]}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"server did not open port {port} within {timeout}s")


@dataclass
class Running:
    port: int
    staff: str  # all scopes, any patient
    reader: str  # read:schedule only
    portal: str  # all scopes, bound to DERM_PATIENT
    twin: str  # same client_id as portal, read:schedule only, bound to another patient
    revokee: str  # read:schedule; test_revoking_a_token_needs_no_restart revokes it
    tokens_file: str
    audit_log: str
    db_path: str  # an encrypted file database
    data_key: DataKey

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Running]:
    tokens_file = str(tmp_path_factory.mktemp("auth") / "tokens.json")
    staff = issue(tokens_file, "front-desk", list(ALL_SCOPES), None)
    reader = issue(tokens_file, "kiosk", ["read:schedule"], None)
    portal = issue(tokens_file, "portal", list(ALL_SCOPES), DERM_PATIENT)
    twin = issue(tokens_file, "portal", ["read:schedule"], "PAT-0008")
    revokee = issue(tokens_file, "revokee", ["read:schedule"], None)
    assert staff not in Path(tokens_file).read_text(), "raw tokens must never be stored"

    root = Path(tokens_file).parent
    key = DataKey.generate()
    port = _free_port()
    env = {
        **os.environ,
        "MCP_OPS_HOST": "127.0.0.1",
        "MCP_OPS_PORT": str(port),
        "MCP_OPS_TOKENS_FILE": tokens_file,
        "MCP_OPS_MAX_BODY_BYTES": str(MAX_BODY_BYTES),
        "MCP_OPS_AUDIT_LOG": str(root / "audit.log"),
        "MCP_OPS_DATA_KEY": key.to_text(),
        "MCP_OPS_DB": str(root / "clinic.db"),
    }
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "mcp_ops_server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port, proc)
        yield Running(
            port,
            staff,
            reader,
            portal,
            twin,
            revokee,
            tokens_file,
            str(root / "audit.log"),
            str(root / "clinic.db"),
            key,
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@asynccontextmanager
async def _session(url: str, token: str) -> AsyncIterator[ClientSession]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=300)) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session


def _raw(
    url: str,
    body: bytes | None,
    token: str | None,
    method: str = "POST",
    session_id: str | None = None,
) -> tuple[int, str | None]:
    """Returns (status, mcp-session-id response header)."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session_id:
        headers["mcp-session-id"] = session_id
    req = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - local test server
            return resp.status, resp.headers.get("mcp-session-id")
    except urllib.error.HTTPError as exc:
        return exc.code, None


def _raw_post(url: str, body: bytes, token: str | None) -> int:
    return _raw(url, body, token)[0]


INIT = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "raw", "version": "0"},
        },
    }
).encode()


def test_healthz_needs_no_token_and_reveals_no_data(server: Running) -> None:
    url = f"http://127.0.0.1:{server.port}/healthz"
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - local test server
        assert resp.status == 200
        assert json.loads(resp.read()) == {"status": "ok", "version": __version__}
    # The probe is not a way around auth: /mcp still refuses an unauthenticated request.
    assert _raw_post(server.url, INIT, None) == 401


def test_missing_or_wrong_token_is_rejected_at_http_layer(server: Running) -> None:
    assert _raw_post(server.url, INIT, None) == 401
    assert _raw_post(server.url, INIT, "not-a-real-token") == 401


def test_tokens_sharing_a_client_id_cannot_touch_each_others_sessions(server: Running) -> None:
    """Codex review, Part 3: sessions were keyed by client_id only, so a patient token could
    DELETE another token's session. Each token now has its own subject."""
    status, session_id = _raw(server.url, INIT, server.portal)
    assert status == 200 and session_id
    ping = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode()
    assert _raw(server.url, ping, server.twin, session_id=session_id)[0] == 404
    assert _raw(server.url, None, server.twin, method="DELETE", session_id=session_id)[0] == 404
    assert _raw(server.url, ping, server.portal, session_id=session_id)[0] == 200


def test_oversized_body_is_rejected(server: Running) -> None:
    padded = json.loads(INIT)
    padded["params"]["clientInfo"]["name"] = "x" * (MAX_BODY_BYTES + 1)
    assert _raw_post(server.url, json.dumps(padded).encode(), server.staff) == 413


def test_server_refuses_to_start_without_tokens_file() -> None:
    env = {k: v for k, v in os.environ.items() if k != "MCP_OPS_TOKENS_FILE"}
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "mcp_ops_server"],
        env={**env, "MCP_OPS_PORT": str(_free_port())},
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode != 0
    assert b"MCP_OPS_TOKENS_FILE is not set" in proc.stderr


def test_server_refuses_to_start_when_tokens_file_is_missing(tmp_path: Path) -> None:
    missing = tmp_path / "tokens.json"
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "mcp_ops_server"],
        env={
            **os.environ,
            **safeguard_env("MCP_OPS_", tmp_path),
            "MCP_OPS_TOKENS_FILE": str(missing),
            "MCP_OPS_PORT": str(_free_port()),
        },
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode != 0
    assert f"Tokens file {missing} not found".encode() in proc.stderr
    assert b"Traceback" not in proc.stderr


async def _health(server: Running) -> dict:
    async with _session(server.url, server.staff) as session:
        tools = await session.list_tools()
        assert "health" in {t.name for t in tools.tools}
        result = await session.call_tool("health", {})
        assert result.is_error is False
        assert result.structured_content is not None
        return result.structured_content


def test_health_over_streamable_http(server: Running) -> None:
    assert asyncio.run(_health(server)) == {"status": "ok", "version": __version__}


async def _scope_and_patient_checks(server: Running) -> dict[str, str]:
    out = {}
    async with _session(server.url, server.reader) as session:
        ok = await session.call_tool("search_providers", {"specialty": "cardiology"})
        out["reader_search"] = "ok" if not ok.is_error else ok.content[0].text
        denied = await session.call_tool(
            "book_appointment", {"patient_id": "PAT-0008", "slot_id": "SLT-00002"}
        )
        out["reader_book"] = denied.content[0].text
        refs = await session.call_tool("get_referral_status", {"patient_id": DERM_PATIENT})
        out["reader_referrals"] = refs.content[0].text
    async with _session(server.url, server.portal) as session:
        own = await session.call_tool("get_referral_status", {"patient_id": DERM_PATIENT})
        out["portal_own"] = "ok" if not own.is_error else own.content[0].text
        other = await session.call_tool("get_referral_status", {"patient_id": "PAT-0002"})
        out["portal_other"] = other.content[0].text
    return out


def test_scopes_and_patient_binding_over_http(server: Running) -> None:
    out = asyncio.run(_scope_and_patient_checks(server))
    assert out["reader_search"] == "ok"
    assert "lacks the write:schedule scope" in out["reader_book"]
    assert "lacks the read:referrals scope" in out["reader_referrals"]
    assert out["portal_own"] == "ok"
    assert f"may only act for patient {DERM_PATIENT}" in out["portal_other"]


async def _booking_flow(server: Running) -> list[str]:
    """The task an agent will be evaluated on: find slot, check referral, book, cancel."""
    steps = []
    async with _session(server.url, server.staff) as session:

        async def ok(tool: str, **args: object) -> dict:
            result = await session.call_tool(tool, args)
            assert result.is_error is False, result.content
            assert result.structured_content is not None
            steps.append(tool)
            return result.structured_content

        providers = await ok("search_providers", specialty="dermatology")
        provider = providers["providers"][0]
        assert provider["requires_referral"] is True

        open_slots = await ok(
            "list_open_slots", provider_id=provider["provider_id"], start_date=WEEK_ONE
        )
        busy = seeded_busy_starts(DERM_PATIENT)
        slot = next(s for s in open_slots["slots"] if s["start"] not in busy)

        # Booking without the referral is refused, and the error reaches the client.
        refused = await session.call_tool(
            "book_appointment", {"patient_id": DERM_PATIENT, "slot_id": slot["slot_id"]}
        )
        assert refused.is_error is True
        assert "requires an approved referral" in refused.content[0].text
        steps.append("book_appointment:refused")

        referrals = await ok(
            "get_referral_status", patient_id=DERM_PATIENT, specialty="dermatology"
        )
        approved = [r for r in referrals["referrals"] if r["status"] == "approved"]
        assert [r["referral_id"] for r in approved] == [DERM_REFERRAL]

        appt = await ok(
            "book_appointment",
            patient_id=DERM_PATIENT,
            slot_id=slot["slot_id"],
            referral_id=DERM_REFERRAL,
        )
        assert appt["status"] == "booked" and appt["start"] == slot["start"]

        after = await ok(
            "list_open_slots", provider_id=provider["provider_id"], start_date=WEEK_ONE
        )
        assert slot["slot_id"] not in {s["slot_id"] for s in after["slots"]}

        cancelled = await ok(
            "cancel_appointment", appointment_id=appt["appointment_id"], patient_id=DERM_PATIENT
        )
        assert cancelled["status"] == "cancelled"

        reopened = await ok(
            "list_open_slots", provider_id=provider["provider_id"], start_date=WEEK_ONE
        )
        assert slot["slot_id"] in {s["slot_id"] for s in reopened["slots"]}
    return steps


def test_booking_flow_over_streamable_http(server: Running) -> None:
    steps = asyncio.run(_booking_flow(server))
    assert steps == [
        "search_providers",
        "list_open_slots",
        "book_appointment:refused",
        "get_referral_status",
        "book_appointment",
        "list_open_slots",
        "cancel_appointment",
        "list_open_slots",
    ]


def test_container_smoke_script_passes_against_a_real_server(
    server: Running, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CI runs scripts/smoke_test.py against the Docker image; keep it working here too."""
    from scripts import smoke_test

    monkeypatch.setenv("MCP_URL", f"http://127.0.0.1:{server.port}")
    monkeypatch.setenv("MCP_TOKEN", server.staff)
    smoke_test.main()
    out = capsys.readouterr().out
    assert "FAIL" not in out and out.rstrip().endswith("smoke test passed")


def test_smoke_script_catches_writes_that_do_not_persist() -> None:
    """A server that answers "booked"/"cancelled" but changes nothing must fail the smoke test."""
    from types import SimpleNamespace

    from scripts import smoke_test

    slots = [{"slot_id": f"SLT-0000{i}", "start": "", "end": ""} for i in (1, 2)]
    appointment = {"appointment_id": "APT-00999", "patient_id": smoke_test.PATIENT}
    replies = {
        "health": {"status": "ok"},
        "search_providers": {"providers": [{"provider_id": "PRV-003"}]},
        "list_open_slots": {"slots": slots},  # never changes: the write did not happen
        "book_appointment": {**appointment, "status": "booked", "slot_id": "SLT-00001"},
        "cancel_appointment": {**appointment, "status": "cancelled", "slot_id": "SLT-00001"},
    }

    class NonPersistingSession:
        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in [*replies, "x"]])

        async def call_tool(self, name: str, args: dict) -> SimpleNamespace:
            return SimpleNamespace(is_error=False, content=[], structured_content=replies[name])

    with pytest.raises(SystemExit):
        asyncio.run(smoke_test.exercise(NonPersistingSession()))


# ---- Part 9 safeguards on the real server ----------------------------------------------------


def test_revoking_a_token_needs_no_restart(server: Running) -> None:
    """The README used to say: remove the token's line, then restart every server. Now the
    running server re-reads the tokens file and refuses a revoked token on the next request."""
    assert _raw_post(server.url, INIT, server.revokee) == 200
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "mcp_ops_server.auth", "revoke", "--file", server.tokens_file]
        + ["--client", "revokee"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert "revoked" in proc.stdout
    assert _raw_post(server.url, INIT, server.revokee) == 401
    assert _raw_post(server.url, INIT, server.staff) == 200  # the others are untouched


async def _calls_to_audit(server: Running) -> str:
    async with _session(server.url, server.portal) as session:
        assert not (
            await session.call_tool("get_referral_status", {"patient_id": DERM_PATIENT})
        ).is_error
        refused = await session.call_tool("get_referral_status", {"patient_id": "PAT-0008"})
        assert refused.is_error
        bad = await session.call_tool("search_providers", {"limit": 999})
        assert bad.is_error
    return DERM_PATIENT


def test_every_call_is_audited_without_its_arguments(server: Running) -> None:
    asyncio.run(_calls_to_audit(server))
    raw = Path(server.audit_log).read_bytes()
    assert b"PAT-" not in raw and b"get_referral_status" not in raw  # encrypted on disk
    count, _ = audit.verify(server.audit_log, server.data_key)
    records = list(audit.records(server.audit_log, server.data_key))
    assert len(records) == count > 0
    portal = [r for r in records if r["client_id"] == "portal" and r["tool"] != "health"]
    last3 = [(r["tool"], r["scope"], r["subject"], r["outcome"]) for r in portal[-3:]]
    assert last3 == [
        ("get_referral_status", "read:referrals", DERM_PATIENT, "ok"),
        ("get_referral_status", "read:referrals", "PAT-0008", "denied"),
        ("search_providers", None, None, "invalid"),
    ]
    token_id = audit.hashlib.sha256(server.portal.encode()).hexdigest()[:12]
    assert {r["token_id"] for r in portal} == {token_id}
    assert all(set(r) == set(audit.RECORD_KEYS) for r in records)


def test_the_database_file_holds_no_plaintext(server: Running) -> None:
    asyncio.run(_booking_flow(server))  # writes, so the file was re-encrypted since start
    raw = Path(server.db_path).read_bytes()
    assert raw.startswith(b"MCPOPS-ENC-SQLITE-1")
    for plaintext in (b"SQLite format 3", b"PAT-0", b"PRV-0", b"SLT-0", b"booked", b"primary_care"):
        assert plaintext not in raw
    assert not Path(server.db_path + "-journal").exists()
    assert not Path(server.db_path + "-wal").exists()
