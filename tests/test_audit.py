"""The audit log (Part 9): one encrypted, hash-chained record per tool call, no arguments."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest
from conftest import DERM_PATIENT, NO_REFERRAL_PATIENT, as_principal, call, first_open_slot
from mcp.server.mcpserver.exceptions import ToolError

from mcp_ops_kit import audit
from mcp_ops_kit.audit import AuditLog, AuditTamperedError
from mcp_ops_kit.crypto import DataKey, seal, unseal
from mcp_ops_kit.errors import ConfigError
from mcp_ops_server.backends.sqlite import SqliteBackend
from mcp_ops_server.server import build_server


@pytest.fixture
def key() -> DataKey:
    return DataKey.generate()


@pytest.fixture
def log_path(tmp_path: Path) -> str:
    return str(tmp_path / "audit.log")


def _write(path: str, key: DataKey, n: int) -> None:
    log = AuditLog(path, key)
    for i in range(n):
        log.append(client_id=f"c{i}", tool="health", outcome="ok")
    log.close()


def _lines(path: str) -> list[bytes]:
    return Path(path).read_bytes().splitlines(keepends=True)


def _reseal(key: DataKey, line: bytes, change: dict) -> bytes:
    """Decrypt a line, change its record, and encrypt it again with the real encryption
    key: an attacker who has the encryption subkey but not the MAC subkey."""
    outer = json.loads(line)
    enc = key.subkey("audit-enc")
    aad = f"mcp-ops-kit/audit/v1/{outer['seq']}".encode()
    blob = base64.urlsafe_b64decode(outer["ct"] + "==")
    record = json.loads(unseal(enc, blob, aad)) | change
    data = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ct = base64.urlsafe_b64encode(seal(enc, data, aad)).decode().rstrip("=")
    return json.dumps({"seq": outer["seq"], "ct": ct}).encode() + b"\n"


# ---- the log itself ------------------------------------------------------------------------


def test_records_chain_and_survive_a_restart(log_path: str, key: DataKey) -> None:
    _write(log_path, key, 2)
    _write(log_path, key, 1)  # a restarted server continues the chain
    records = list(audit.records(log_path, key))
    assert [r["seq"] for r in records] == [1, 2, 3]
    assert records[0]["prev"] == audit.GENESIS
    chain = audit._Keys(key).chain
    assert records[1]["prev"] == chain(records[0]) and records[2]["prev"] == chain(records[1])
    assert audit.verify(log_path, key) == (3, chain(records[2]))
    assert all(set(r) == set(audit.RECORD_KEYS) for r in records)


def test_the_file_is_ciphertext(log_path: str, key: DataKey) -> None:
    log = AuditLog(log_path, key)
    log.append(client_id="front-desk", tool="book_appointment", subject="PAT-0008", outcome="ok")
    log.close()
    raw = Path(log_path).read_bytes()
    for plaintext in (b"front-desk", b"book_appointment", b"PAT-0008", b"outcome"):
        assert plaintext not in raw
    assert Path(log_path).stat().st_mode & 0o777 == 0o600


def test_an_edited_record_is_detected_by_the_chain(log_path: str, key: DataKey) -> None:
    """Even re-encrypted with the right encryption key, an edit breaks the HMAC chain at the
    next record: without the MAC key, the chain cannot be recomputed."""
    _write(log_path, key, 3)
    lines = _lines(log_path)
    lines[1] = _reseal(key, lines[1], {"outcome": "denied"})
    Path(log_path).write_bytes(b"".join(lines))
    with pytest.raises(AuditTamperedError, match="line 3: chain broken"):
        audit.verify(log_path, key)


@pytest.mark.parametrize(
    ("tamper", "line", "reason"),
    [
        (
            lambda ls: ls[:1] + [ls[1].replace(b'"ct":"', b'"ct":"A')] + ls[2:],
            2,
            "does not decrypt",
        ),
        (lambda ls: [ls[0], ls[2], ls[3]], 2, "sequence 3 where 2 was expected"),
        (lambda ls: [ls[0], ls[2], ls[1], ls[3]], 2, "sequence 3 where 2 was expected"),
        (lambda ls: ls[:3] + [ls[3][:-1]], 4, "incomplete last line"),
        (lambda ls: [ls[0], ls[1].replace(b'"seq":2', b'"seq":3'), *ls[2:]], 2, "does not decrypt"),
    ],
    ids=["edited-ciphertext", "deleted", "reordered", "torn-write", "renumbered"],
)
def test_tampering_is_detected_at_its_line(
    log_path: str, key: DataKey, tamper: object, line: int, reason: str
) -> None:
    _write(log_path, key, 4)
    Path(log_path).write_bytes(b"".join(tamper(_lines(log_path))))  # type: ignore[operator]
    with pytest.raises(AuditTamperedError) as exc:
        audit.verify(log_path, key)
    assert exc.value.line == line and reason in str(exc.value)


def test_a_server_refuses_to_append_to_a_log_it_cannot_read(log_path: str, key: DataKey) -> None:
    _write(log_path, key, 1)
    with pytest.raises(ConfigError, match="wrong data key"):
        AuditLog(log_path, DataKey.generate())
    Path(log_path).write_bytes(Path(log_path).read_bytes()[:-1])
    with pytest.raises(ConfigError, match="incomplete"):
        AuditLog(log_path, key)


def test_one_writer_at_a_time(log_path: str, key: DataKey) -> None:
    first = AuditLog(log_path, key)
    with pytest.raises(ConfigError, match="in use by another process"):
        AuditLog(log_path, key)
    first.close()
    AuditLog(log_path, key).close()


def test_the_cli_verifies_and_reports_tampering(
    log_path: str, key: DataKey, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _write(log_path, key, 2)
    monkeypatch.setenv("TEST_AUDIT_KEY", key.to_text())
    audit.main(["verify", "--file", log_path, "--key-env", "TEST_AUDIT_KEY"])
    assert capsys.readouterr().out.startswith("ok: 2 records, chain intact; last seq 2, head ")
    audit.main(["show", "--file", log_path, "--key-env", "TEST_AUDIT_KEY"])
    shown = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["client_id"] for r in shown] == ["c0", "c1"]
    Path(log_path).write_bytes(_lines(log_path)[1])
    with pytest.raises(SystemExit, match="TAMPERED: .* line 1: sequence 2 where 1"):
        audit.main(["verify", "--file", log_path, "--key-env", "TEST_AUDIT_KEY"])


# ---- inside a server -----------------------------------------------------------------------


def _audited_server(log_path: str, key: DataKey, **kwargs: object):  # noqa: ANN202
    log = AuditLog(log_path, key)
    return build_server(SqliteBackend(), audit=log, **kwargs), log


def _try(server: object, tool: str, **args: object) -> None:
    try:
        asyncio.run(server.call_tool(tool, args))  # type: ignore[attr-defined]
    except ToolError:
        pass


def test_each_outcome_is_recorded(log_path: str, key: DataKey) -> None:
    server, log = _audited_server(log_path, key, rate_limit_per_minute=7)
    slot = first_open_slot(server, "PRV-001", NO_REFERRAL_PATIENT)  # 1: ok
    call(server, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot)  # 2: ok
    _try(server, "book_appointment", patient_id=DERM_PATIENT, slot_id=slot)  # 3: taken
    _try(server, "search_providers", limit=999)  # 4: invalid arguments
    _try(server, "no_such_tool")  # 5: unknown tool
    with as_principal(client_id="portal", patient_id=DERM_PATIENT):
        _try(server, "get_referral_status", patient_id=NO_REFERRAL_PATIENT)  # 6: other patient
    with as_principal(client_id="kiosk", scopes=["read:schedule"]):
        _try(server, "get_referral_status", patient_id=DERM_PATIENT)  # 7: lacks scope
    for _ in range(3):
        _try(server, "health")  # test-staff calls 6 and 7 pass, the 8th is over its limit of 7
    log.close()
    rows = [
        (r["client_id"], r["tool"], r["scope"], r["subject"], r["outcome"], r["error"])
        for r in audit.records(log_path, key)
    ]
    staff, kiosk = "test-staff", "kiosk"
    assert rows == [
        (staff, "list_open_slots", "read:schedule", None, "ok", None),
        (staff, "book_appointment", "write:schedule", NO_REFERRAL_PATIENT, "ok", None),
        (staff, "book_appointment", "write:schedule", DERM_PATIENT, "error", "ConflictError"),
        (staff, "search_providers", None, None, "invalid", "ToolError"),
        (staff, "no_such_tool", None, None, "invalid", "ToolError"),
        (
            "portal",
            "get_referral_status",
            "read:referrals",
            NO_REFERRAL_PATIENT,
            "denied",
            "ToolError",
        ),
        (kiosk, "get_referral_status", "read:referrals", DERM_PATIENT, "denied", "ToolError"),
        (staff, "health", None, None, "ok", None),
        (staff, "health", None, None, "ok", None),
        (staff, "health", None, None, "rate_limited", "ToolError"),
    ]


def test_arguments_and_results_never_reach_the_log(log_path: str, key: DataKey) -> None:
    server, log = _audited_server(log_path, key)
    slot = first_open_slot(server, "PRV-004", DERM_PATIENT)
    booked = call(
        server, "book_appointment", patient_id=DERM_PATIENT, slot_id=slot, referral_id="REF-0001"
    )
    call(server, "search_providers", name="Dr")
    log.close()
    text = json.dumps(list(audit.records(log_path, key)))
    for value in (slot, "REF-0001", booked["appointment_id"], booked["provider_name"], '"Dr"'):
        assert value not in text


def test_the_token_is_identified_by_its_hash_prefix(log_path: str, key: DataKey) -> None:
    server, log = _audited_server(log_path, key)
    call(server, "health")
    log.close()
    (record,) = audit.records(log_path, key)
    # conftest's as_principal uses the raw token "test-token"
    assert record["token_id"] == hashlib.sha256(b"test-token").hexdigest()[:12]


def test_if_a_record_cannot_be_written_the_server_stops_acting(
    log_path: str, key: DataKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call already made returns its real result (it happened; reporting a failure
    would be false), and every later call is refused until the log works again."""
    server, log = _audited_server(log_path, key)
    slot = first_open_slot(server, "PRV-001", NO_REFERRAL_PATIENT)

    def disk_full(**values: object) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(log, "append", disk_full)
    booked = call(server, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot)
    assert booked["status"] == "booked"
    assert log.failed is not None and "No space left" in log.failed
    with pytest.raises(ToolError, match="audit log is unavailable"):
        asyncio.run(server.call_tool("health", {}))
    with pytest.raises(ToolError, match="audit log is unavailable"):
        asyncio.run(
            server.call_tool(
                "cancel_appointment", {"appointment_id": "APT-00001", "patient_id": "PAT-0001"}
            )
        )


def test_a_server_without_an_audit_log_records_nothing(log_path: str) -> None:
    """In-process servers (unit tests) may run unaudited; served ones never do (see
    test_http_integration and test_safeguards)."""
    call(build_server(SqliteBackend()), "health")
    assert not Path(log_path).exists()


def test_a_cancelled_call_is_still_recorded_with_an_outcome(log_path: str, key: DataKey) -> None:
    """A client that disconnects mid-call cancels it (CancelledError is a BaseException)."""
    from mcp_ops_kit import server as kit
    from mcp_ops_server.auth import AUTH

    def register(server: object, guard: object) -> None:
        @kit.tool(server)  # type: ignore[arg-type]
        async def slow() -> dict:
            """Checks a scope, then is cancelled."""
            guard.check("read:schedule")  # type: ignore[attr-defined]
            raise asyncio.CancelledError

    log = AuditLog(log_path, key)
    server = kit.build_server("t", "0", AUTH, register, audit=log)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(server.call_tool("slow", {}))
    log.close()
    (record,) = audit.records(log_path, key)
    assert (record["tool"], record["outcome"], record["error"]) == (
        "slow",
        "error",
        "CancelledError",
    )
