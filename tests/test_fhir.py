"""FHIR backend specifics. Tool behaviour itself is covered by test_tools.py on every backend.

Unit tests here need no FHIR server (time zones, the seed Bundle, paging and errors against
a fake transport, fail-closed start-up). Tests marked ``fhir`` run against the real FHIR
server (CI: HAPI) and prove what a fake cannot: that it enforces ``If-Match``, that two
concurrent books of a slot leave one booking, and that reset really restores the seed.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx2
import pytest
from conftest import NO_REFERRAL_PATIENT, PRIMARY_PROVIDER, SECOND_FREE_PATIENT, safeguard_env

from mcp_ops_kit import audit
from mcp_ops_kit.crypto import DataKey
from mcp_ops_server import seed
from mcp_ops_server.auth import ALL_SCOPES, issue
from mcp_ops_server.backends import fhir_seed
from mcp_ops_server.backends.base import (
    BusyError,
    CallTimeoutError,
    ConflictError,
    OutcomeUnknownError,
    StoreError,
)
from mcp_ops_server.backends.fhir import (
    SEQUENCE_ID,
    BackendUnavailableError,
    FhirBackend,
    _VersionConflict,
    from_instant,
    put,
    sequence_resource,
    to_instant,
)
from mcp_ops_server.backends.sqlite import SqliteBackend

BASE = "http://fhir.test/fhir"

# ---- time zones --------------------------------------------------------------------------


def test_instants_are_utc_and_round_trip_to_clinic_time() -> None:
    local = datetime(2026, 11, 2, 9, 0)
    assert to_instant(local) == "2026-11-02T14:00:00Z"  # clinic is UTC-05:00
    assert from_instant("2026-11-02T14:00:00Z") == local


@pytest.mark.parametrize(
    "instant",
    [
        "2026-11-02T14:00:00Z",
        "2026-11-02T14:00:00+00:00",
        "2026-11-02T09:00:00-05:00",
        "2026-11-02T19:30:00+05:30",
        "2026-11-03T03:00:00+13:00",
    ],
)
def test_any_offset_the_server_returns_means_the_same_clinic_time(instant: str) -> None:
    """A FHIR server may hand back the same instant with another offset."""
    assert from_instant(instant) == datetime(2026, 11, 2, 9, 0)


def test_late_evening_slot_stays_on_its_clinic_day() -> None:
    # 22:30 clinic time is 03:30 UTC the next day; the day a slot is listed under is local.
    assert to_instant(datetime(2026, 11, 2, 22, 30)) == "2026-11-03T03:30:00Z"
    assert from_instant("2026-11-03T03:30:00Z").date() == date(2026, 11, 2)


def test_instant_without_offset_is_rejected() -> None:
    with pytest.raises(ValueError, match="without an offset"):
        from_instant("2026-11-02T14:00:00")


# ---- the seed as FHIR --------------------------------------------------------------------


def _resources() -> list[dict[str, Any]]:
    return [e["resource"] for b in fhir_seed.bundles() for e in b["entry"]]


def test_seed_bundles_are_deterministic_puts() -> None:
    assert json.dumps(fhir_seed.bundles()) == json.dumps(fhir_seed.bundles())
    for bundle in fhir_seed.bundles():
        assert bundle["type"] == "transaction"
        for e in bundle["entry"]:
            r = e["resource"]
            assert e["request"] == {"method": "PUT", "url": f"{r['resourceType']}/{r['id']}"}


def test_seed_bundles_hold_the_whole_seed_with_the_same_ids() -> None:
    data = seed.generate()
    by_type: dict[str, set[str]] = {}
    for r in _resources():
        by_type.setdefault(r["resourceType"], set()).add(r["id"])
    assert by_type["Patient"] == set(data.patient_ids)
    assert by_type["PractitionerRole"] == {p.id for p in data.providers}
    assert by_type["Slot"] == {s.id for s in data.slots}
    assert by_type["ServiceRequest"] == {r.id for r in data.referrals}
    assert by_type["Appointment"] == {f"APT-{n:05d}" for n in range(1, len(data.appointments) + 1)}
    busy = {r["id"] for r in _resources() if r["resourceType"] == "Slot" and r["status"] == "busy"}
    assert busy == {a.slot_id for a in data.appointments}


def test_every_reference_in_the_seed_resolves() -> None:
    present = {f"{r['resourceType']}/{r['id']}" for r in _resources()}

    def refs(node: object) -> Iterator[str]:
        if isinstance(node, dict):
            if isinstance(node.get("reference"), str):
                yield node["reference"]
            for v in node.values():
                yield from refs(v)
        elif isinstance(node, list):
            for v in node:
                yield from refs(v)

    missing = {r for res in _resources() for r in refs(res)} - present
    assert missing == set()


def test_seed_patients_are_identifiers_only() -> None:
    """Synthetic data only: a Patient has an ID and nothing that could describe a person."""
    for r in _resources():
        if r["resourceType"] == "Patient":
            assert set(r) == {"resourceType", "id", "identifier"}, r


def test_seed_export_cli_writes_the_bundles(tmp_path: Any) -> None:
    out = tmp_path / "bundles.json"
    fhir_seed.main(["export", "--out", str(out)])
    assert json.loads(out.read_text()) == fhir_seed.bundles()


# ---- HTTP behaviour against a fake FHIR server -------------------------------------------


def _fake(handler: Callable[[httpx2.Request], httpx2.Response], **kw: Any) -> FhirBackend:
    return FhirBackend(BASE, transport=httpx2.MockTransport(handler), **kw)


def _bundle(resources: list[dict], next_url: str | None = None) -> dict:
    links = [{"relation": "next", "url": next_url}] if next_url else []
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "link": links,
        "entry": [{"resource": r} for r in resources],
    }


def _role(n: int) -> list[dict]:
    pid = f"PRV-{n:03d}"
    return [
        {
            "resourceType": "PractitionerRole",
            "id": pid,
            "practitioner": {"reference": f"Practitioner/{pid}"},
            "specialty": [{"coding": [{"system": "urn:mcp-ops:specialty", "code": "pediatrics"}]}],
            "location": [{"reference": "Location/north"}],
        },
        {"resourceType": "Practitioner", "id": pid, "name": [{"text": f"Dr. Test {n}"}]},
    ]


def test_search_follows_every_next_page() -> None:
    pages = {
        None: _bundle(_role(1), f"{BASE}?_getpages=x&page=2"),
        "2": _bundle(_role(2), f"{BASE}?_getpages=x&page=3"),
        "3": _bundle(_role(3)),
    }
    seen = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(str(req.url))
        return httpx2.Response(200, json=pages[req.url.params.get("page")])

    out = _fake(handler).search_providers()
    assert [p["provider_id"] for p in out["providers"]] == ["PRV-001", "PRV-002", "PRV-003"]
    assert len(seen) == 3


def test_search_refuses_a_next_link_to_another_server() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        assert req.url.host == "fhir.test", "must never be fetched"
        return httpx2.Response(200, json=_bundle(_role(1), "http://elsewhere.test/fhir?page=2"))

    with pytest.raises(StoreError, match="another server"):
        _fake(handler).search_providers()


def test_every_request_bypasses_the_search_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the backend's own client (its headers). Proxies from the environment must not
    apply to an injected transport: Codex's run reached a real proxy here (PR #16)."""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")  # nothing listens there
    headers = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        headers.append(req.headers.get("cache-control"))
        return httpx2.Response(200, json=_bundle(_role(1)))

    _fake(handler).search_providers()
    assert headers == ["no-cache"]


def test_unreachable_server_mid_call_is_a_retryable_error() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=req)

    with pytest.raises(BusyError, match="unavailable. Retry"):
        _fake(handler).search_providers()


def test_server_error_text_is_not_echoed_to_the_agent() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"resourceType": "OperationOutcome", "issue": [
            {"diagnostics": "PAT-0001 secret detail"}]})  # fmt: skip

    with pytest.raises(StoreError) as exc:
        _fake(handler).search_providers()
    assert "secret" not in str(exc.value) and "HTTP 400" in str(exc.value)


APPT = {
    "resourceType": "Appointment", "id": "APT-00001", "meta": {"versionId": "2"},
    "status": "booked", "start": "2026-11-02T15:00:00Z", "end": "2026-11-02T15:30:00Z",
    "slot": [{"reference": "Slot/SLT-00003"}],
    "participant": [{"actor": {"reference": "Patient/PAT-0008"}},
                    {"actor": {"reference": "PractitionerRole/PRV-001"}}],
}  # fmt: skip


def _booking_server(
    commit: int | Callable[[dict, dict], httpx2.Response],
) -> tuple[Callable, list[dict], list[str]]:
    """A fake FHIR server with one free slot (SLT-00002) and one booked appointment
    (APT-00001 on SLT-00003). ``commit`` is the status every transaction answers, or a
    function of (bundle, reads) that answers it and may change what later reads return."""
    slot = {"resourceType": "Slot", "id": "SLT-00002", "meta": {"versionId": "3"},
            "schedule": {"reference": "Schedule/PRV-001"}, "status": "free",
            "start": "2026-11-02T14:30:00Z", "end": "2026-11-02T15:00:00Z"}  # fmt: skip
    reads = {
        "Patient/PAT-0008": {"resourceType": "Patient", "id": "PAT-0008"},
        "Slot/SLT-00002": slot,
        "Slot/SLT-00003": {**slot, "id": "SLT-00003", "status": "busy"},
        "Appointment/APT-00001": APPT,
        f"Basic/{SEQUENCE_ID}": {**sequence_resource(128), "meta": {"versionId": "9"}},
    }
    commits: list[dict] = []
    gets: list[str] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        path = req.url.path.removeprefix("/fhir/").rstrip("/")
        gets.append(path)
        if req.method == "POST":
            bundle = json.loads(req.content)
            commits.append(bundle)
            if callable(commit):
                return commit(bundle, reads)
            return httpx2.Response(commit, json={"resourceType": "Bundle"})
        if path in reads:
            return httpx2.Response(200, json=reads[path])
        if path == "PractitionerRole":
            return httpx2.Response(200, json=_bundle(_role(1)))
        if "/" in path:
            return httpx2.Response(404, json={"resourceType": "OperationOutcome"})
        return httpx2.Response(200, json=_bundle([]))  # no clashing appointments

    return handler, commits, gets


def _lands_then_hangs(seconds: float) -> Callable[[dict, dict], httpx2.Response]:
    """The server commits the transaction, then the answer never arrives in time."""

    def commit(bundle: dict, reads: dict) -> httpx2.Response:
        for e in bundle["entry"]:
            reads[e["request"]["url"]] = e["resource"]
        time.sleep(seconds)
        return httpx2.Response(200, json={"resourceType": "Bundle"})

    return commit


def _hangs(seconds: float) -> Callable[[dict, dict], httpx2.Response]:
    """No answer in time, and nothing visible yet (the commit may still be in flight)."""

    def commit(bundle: dict, reads: dict) -> httpx2.Response:
        time.sleep(seconds)
        return httpx2.Response(200, json={"resourceType": "Bundle"})

    return commit


def test_booking_commits_slot_and_sequence_with_if_match_in_one_transaction() -> None:
    handler, commits, gets = _booking_server(200)
    out = _fake(handler).book_appointment("PAT-0008", "SLT-00002")
    # The sequence is read first: a booking that commits after this read moves it, so a
    # slot read later cannot be stale without the sequence If-Match failing too.
    assert gets[0] == f"Basic/{SEQUENCE_ID}"
    assert out["appointment_id"] == "APT-00129" and out["start"] == "2026-11-02T09:30:00"
    [bundle] = commits
    assert bundle["type"] == "transaction"
    requests = [(e["request"]["url"], e["request"].get("ifMatch")) for e in bundle["entry"]]
    assert requests == [
        ("Slot/SLT-00002", 'W/"3"'),  # the version that was checked as free
        (f"Basic/{SEQUENCE_ID}", 'W/"9"'),  # the version read before every check
        ("Appointment/APT-00129", None),
    ]
    assert bundle["entry"][0]["resource"]["status"] == "busy"


@pytest.mark.parametrize("status", [409, 412])
@pytest.mark.parametrize("op", ["book", "cancel"])
def test_lost_races_are_retried_until_the_deadline(status: int, op: str) -> None:
    """Every commit answers 409 or 412 (someone else always wins): the write re-checks and
    retries, then gives up with a timeout, having written nothing, at the deadline."""
    handler, commits, _ = _booking_server(status)
    backend = _fake(handler, timeout_s=0.5)
    start = time.monotonic()
    with pytest.raises(CallTimeoutError):
        if op == "book":
            backend.book_appointment("PAT-0008", "SLT-00002")
        else:
            backend.cancel_appointment("APT-00001", "PAT-0008")
    assert len(commits) > 1
    assert time.monotonic() - start < 0.5 + 0.2


def test_a_read_stops_at_the_deadline_however_slow_the_server() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        time.sleep(1.5)
        return httpx2.Response(200, json=_bundle(_role(1)))

    start = time.monotonic()
    with pytest.raises(CallTimeoutError):
        _fake(handler, timeout_s=0.3).search_providers()
    assert time.monotonic() - start < 0.3 + 0.2


class _Dribbler(BaseHTTPRequestHandler):
    """Sends a valid response a few bytes at a time: every network wait is short, but the
    whole answer takes far longer than the call's budget (Codex review, PR #16)."""

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body = json.dumps(_bundle(_role(1))).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/fhir+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for i in range(0, len(body), 40):
            try:
                self.wfile.write(body[i : i + 40])
                self.wfile.flush()
            except OSError:
                return
            time.sleep(0.05)


def test_a_dribbled_response_cannot_stretch_the_deadline() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Dribbler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        backend = FhirBackend(f"http://127.0.0.1:{server.server_port}/fhir", timeout_s=0.3)
        start = time.monotonic()
        with pytest.raises(CallTimeoutError):
            backend.search_providers()
        assert time.monotonic() - start < 0.3 + 0.2
    finally:
        server.shutdown()


def test_an_unanswered_booking_that_landed_is_reported_as_booked() -> None:
    """The commit lands but its answer never comes: never "cancelled" (Codex review, PR #16).
    The backend reads the appointment back and reports what happened."""
    handler, _, _ = _booking_server(_lands_then_hangs(1.0))
    out = _fake(handler, timeout_s=0.3).book_appointment("PAT-0008", "SLT-00002")
    assert out["appointment_id"] == "APT-00129" and out["status"] == "booked"


def test_an_unanswered_booking_not_seen_yet_is_reported_as_unknown() -> None:
    handler, _, _ = _booking_server(_hangs(1.0))
    start = time.monotonic()
    with pytest.raises(OutcomeUnknownError) as exc:
        _fake(handler, timeout_s=0.3).book_appointment("PAT-0008", "SLT-00002")
    assert "may or may not" in str(exc.value) and "APT-00129" in str(exc.value)
    assert "cancelled" not in str(exc.value)
    assert time.monotonic() - start < 0.3 + 0.3 + 0.3  # budget + one read-back + slack


def test_an_unanswered_cancel_is_read_back() -> None:
    handler, _, _ = _booking_server(_lands_then_hangs(1.0))
    out = _fake(handler, timeout_s=0.3).cancel_appointment("APT-00001", "PAT-0008")
    assert out["status"] == "cancelled"
    handler, _, _ = _booking_server(_hangs(1.0))
    with pytest.raises(OutcomeUnknownError, match="may or may not be cancelled"):
        _fake(handler, timeout_s=0.3).cancel_appointment("APT-00001", "PAT-0008")


def test_a_write_that_never_connected_is_retryable() -> None:
    """Connection refused means nothing was sent: a plain "unavailable, retry"."""

    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.method == "POST":
            raise httpx2.ConnectError("refused", request=req)
        return booking(req)

    booking, _, _ = _booking_server(200)
    with pytest.raises(BusyError, match="unavailable. Retry"):
        _fake(handler).book_appointment("PAT-0008", "SLT-00002")


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        ({"metadata": httpx2.ConnectError("refused")}, r"is unreachable \(ConnectError\)"),
        ({"metadata": (200, {"fhirVersion": "5.0.0"})}, "did not return an R4"),
        ({"metadata": (404, {})}, "did not return an R4"),
        ({"metadata": (200, {"fhirVersion": "4.0.1"}), "Basic": (404, {})}, "Load the synthetic"),
    ],
)
def test_start_up_check_fails_closed_with_one_line(responses: dict, message: str) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        r = responses.get(req.url.path.split("/")[2], (200, {}))
        if isinstance(r, Exception):
            raise r
        return httpx2.Response(r[0], json=r[1])

    with pytest.raises(BackendUnavailableError, match=message) as exc:
        _fake(handler).check()
    assert "\n" not in str(exc.value)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"MCP_OPS_FHIR_URL": "http://127.0.0.1:{port}/fhir"}, "is unreachable"),
        ({}, "MCP_OPS_BACKEND=fhir needs MCP_OPS_FHIR_URL."),
        # Plain HTTP to a record system is refused before any request (Codex, PR #24).
        ({"MCP_OPS_FHIR_URL": "http://records.example/fhir"}, "must be https://"),
        ({"MCP_OPS_FHIR_URL": "ftp://127.0.0.1/fhir"}, "must be https://"),
    ],
)
def test_server_refuses_to_start_without_a_usable_fhir_server(
    tmp_path: Any, env: dict, message: str
) -> None:
    tokens = str(tmp_path / "tokens.json")
    issue(tokens, "dev", list(ALL_SCOPES), None)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("MCP_OPS_")}
    extra = {k: v.format(port=_free_port()) for k, v in env.items()}
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "mcp_ops_server"],
        env={
            **clean,
            **safeguard_env("MCP_OPS_", tmp_path),
            "MCP_OPS_BACKEND": "fhir",
            "MCP_OPS_TOKENS_FILE": tokens,
            **extra,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1
    assert message in proc.stderr
    assert "Traceback" not in proc.stderr and len(proc.stderr.strip().splitlines()) == 1


def test_unknown_backend_name_is_rejected() -> None:
    from mcp_ops_server.config import Settings

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MCP_OPS_BACKEND", "postgres")
        with pytest.raises(ValueError, match="sqlite, fhir"):
            Settings.from_env()


# ---- against the real FHIR server --------------------------------------------------------


@pytest.fixture
def fhir(fhir_url: str) -> Iterator[FhirBackend]:
    fhir_seed.reset(fhir_url)
    b = FhirBackend(fhir_url)
    yield b
    b.close()


@pytest.mark.fhir
def test_reset_restores_exactly_the_seed(fhir: FhirBackend, fhir_url: str) -> None:
    sqlite = SqliteBackend()
    slot = fhir.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"][0]["slot_id"]
    fhir.book_appointment(NO_REFERRAL_PATIENT, slot)
    seeded = fhir.snapshot_appointments()[1]
    fhir.cancel_appointment("APT-00001", seeded["patient_id"])
    assert fhir.snapshot_appointments() != sqlite.snapshot_appointments()
    fhir_seed.reset(fhir_url)
    assert fhir.snapshot_appointments() == sqlite.snapshot_appointments()
    for p in ("PRV-001", "PRV-004", seeded["provider_id"]):
        args = (p, seed.SCHEDULE_START, date(2026, 11, 29), 100)
        assert fhir.list_open_slots(*args) == sqlite.list_open_slots(*args)
    # Numbering restarts where the seed ends, as in a fresh SQLite database.
    assert fhir.book_appointment(NO_REFERRAL_PATIENT, slot) == sqlite.book_appointment(
        NO_REFERRAL_PATIENT, slot
    )


@pytest.mark.fhir
def test_loading_the_same_seed_twice_changes_nothing(fhir_url: str) -> None:
    assert fhir_seed.load(fhir_url) is False


@pytest.mark.fhir
@pytest.mark.parametrize("page_size", [1, 7])
def test_results_past_the_first_page_are_never_lost(fhir_url: str, page_size: int) -> None:
    """Tiny pages force every read through several ``next`` links."""
    fhir_seed.reset(fhir_url)
    small, sqlite = FhirBackend(fhir_url, page_size=page_size, timeout_s=30), SqliteBackend()
    assert small.search_providers(limit=50) == sqlite.search_providers(limit=50)
    args = (PRIMARY_PROVIDER, seed.SCHEDULE_START, None, 30)
    assert small.list_open_slots(*args) == sqlite.list_open_slots(*args)
    assert small.get_referral_status("PAT-0002") == sqlite.get_referral_status("PAT-0002")
    if page_size == 7:  # 128 appointments: ~19 pages
        assert small.snapshot_appointments() == sqlite.snapshot_appointments()


@pytest.mark.fhir
def test_server_enforces_if_match_on_the_slot(fhir: FhirBackend) -> None:
    """A Slot version read as free, then changed by someone else, must not be overwritten."""
    slot_id = fhir.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"][0]["slot_id"]
    stale = fhir._read("Slot", slot_id)  # noqa: SLF001
    fhir._transact([put({**stale, "status": "busy"})])  # noqa: SLF001 - someone else's write
    with fhir._call(), pytest.raises(_VersionConflict):  # noqa: SLF001
        fhir._transact([put({**stale, "status": "busy"}, if_match=stale["meta"]["versionId"])])  # noqa: SLF001


@pytest.mark.fhir
def test_server_enforces_if_match_on_the_sequence(fhir: FhirBackend) -> None:
    stale = fhir._read("Basic", SEQUENCE_ID)  # noqa: SLF001
    # A real change: HAPI keeps the version on a PUT that changes nothing.
    moved = sequence_resource(stale["extension"][0]["valueInteger"] + 1)
    fhir._transact([put(moved)])  # noqa: SLF001 - someone else's booking
    with fhir._call(), pytest.raises(_VersionConflict):  # noqa: SLF001
        fhir._transact([put(stale, if_match=stale["meta"]["versionId"])])  # noqa: SLF001


@pytest.mark.fhir
def test_booking_that_loses_the_race_after_its_checks_is_rejected(fhir: FhirBackend) -> None:
    """Deterministic interleaving: B books the slot after A has checked it but before A
    commits. A's transaction must fail its If-Match, re-check, and say "already booked"."""
    slot_id = fhir.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"][0]["slot_id"]
    other = FhirBackend(fhir._base)  # noqa: SLF001
    real = fhir._transact  # noqa: SLF001
    raced = threading.Event()

    def transact_after_rival(entries: list[dict]) -> None:
        if not raced.is_set():
            raced.set()
            other.book_appointment(SECOND_FREE_PATIENT, slot_id)
        real(entries)

    fhir._transact = transact_after_rival  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(ConflictError, match="already booked"):
        fhir.book_appointment(NO_REFERRAL_PATIENT, slot_id)
    booked = [a for a in fhir.snapshot_appointments().values() if a["slot_id"] == slot_id]
    assert [(a["patient_id"], a["status"]) for a in booked] == [(SECOND_FREE_PATIENT, "booked")]


@pytest.mark.fhir
def test_cancel_racing_cancel_cancels_once(fhir: FhirBackend) -> None:
    slot_id = fhir.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"][0]["slot_id"]
    appt = fhir.book_appointment(NO_REFERRAL_PATIENT, slot_id)
    barrier = threading.Barrier(8)

    def attempt(_: int) -> str:
        barrier.wait()
        try:
            fhir.cancel_appointment(appt["appointment_id"], NO_REFERRAL_PATIENT)
            return "ok"
        except ConflictError:
            return "conflict"

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count("ok") == 1 and results.count("conflict") == 7
    open_now = fhir.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"]
    assert slot_id in {s["slot_id"] for s in open_now}


@pytest.mark.fhir
def test_server_on_fhir_passes_the_container_smoke_test(
    fhir_url: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """``python -m mcp_ops_server`` with MCP_OPS_BACKEND=fhir, end to end over HTTP."""
    from test_http_integration import _wait_for_port

    from scripts import smoke_test

    fhir_seed.reset(fhir_url)
    tokens = str(tmp_path / "tokens.json")
    token = issue(tokens, "smoke", list(ALL_SCOPES), None)
    port = _free_port()
    env = {
        **os.environ,
        "MCP_OPS_PORT": str(port),
        "MCP_OPS_TOKENS_FILE": tokens,
        "MCP_OPS_BACKEND": "fhir",
        "MCP_OPS_FHIR_URL": fhir_url,
        **safeguard_env("MCP_OPS_", tmp_path),
    }
    proc = subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "mcp_ops_server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port, proc)
        monkeypatch.setenv("MCP_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("MCP_TOKEN", token)
        smoke_test.main()
        # The audit trail is backend-independent: the smoke test's 7 calls, chain intact.
        key = DataKey.from_text(env["MCP_OPS_DATA_KEY"])
        records = list(audit.records(env["MCP_OPS_AUDIT_LOG"], key))
        assert [r["outcome"] for r in records] == ["ok"] * 7
        assert {r["tool"] for r in records} >= {"book_appointment", "cancel_appointment"}
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    out = capsys.readouterr().out
    assert "FAIL" not in out and out.rstrip().endswith("smoke test passed")


@pytest.mark.fhir
@pytest.mark.parametrize("limit", [1, 3, 20])
def test_past_slots_never_crowd_out_upcoming_ones(fhir_url: str, limit: int) -> None:
    """Cursor Bugbot, PR #16: with "now" mid-week, a range starting on Monday holds many free
    slots already in the past. They must be excluded by the search itself, not after
    ``limit`` results were fetched, or the list comes back short with a wrong ``truncated``."""
    fhir_seed.reset(fhir_url)
    now = datetime(2026, 11, 4, 12, 0)
    fhir = FhirBackend(fhir_url, now=lambda: now)
    sqlite = SqliteBackend(now=lambda: now)
    args = (PRIMARY_PROVIDER, seed.SCHEDULE_START, None, limit)
    got = fhir.list_open_slots(*args)
    assert got == sqlite.list_open_slots(*args)
    assert got["count"] == limit and got["truncated"] is True


@pytest.mark.fhir
def test_reset_refuses_a_seed_that_is_not_loaded(fhir: FhirBackend, fhir_url: str) -> None:
    """Codex review, PR #16: reset toward another seed left 126 wrong bookings."""
    before = fhir.snapshot_appointments()
    with pytest.raises(fhir_seed.SeedMismatchError, match="does not hold seed 20261103"):
        fhir_seed.reset(fhir_url, 20261103)
    assert fhir.snapshot_appointments() == before  # nothing changed
    with pytest.raises(SystemExit, match="fhir_seed load"):
        fhir_seed.main(["reset", "--url", fhir_url, "--seed", "20261103"])


@pytest.mark.fhir
def test_reset_restores_appointment_content_not_only_status(
    fhir: FhirBackend, fhir_url: str
) -> None:
    tampered = fhir._read("Appointment", "APT-00001")  # noqa: SLF001
    tampered.pop("meta")
    tampered["slot"] = [{"reference": "Slot/SLT-00002"}]  # still "booked"
    fhir._transact([put(tampered)])  # noqa: SLF001
    assert fhir.snapshot_appointments() != SqliteBackend().snapshot_appointments()
    fhir_seed.reset(fhir_url)
    assert fhir.snapshot_appointments() == SqliteBackend().snapshot_appointments()


@pytest.mark.fhir
def test_another_seed_loads_and_resets_like_sqlite(fhir_url: str) -> None:
    other = 20261103
    try:
        assert fhir_seed.load(fhir_url, other) is True
        fhir_seed.reset(fhir_url, other)
        fhir = FhirBackend(fhir_url)
        sqlite = SqliteBackend(seed_value=other)
        assert fhir.snapshot_appointments() == sqlite.snapshot_appointments()
        args = ("PRV-002", seed.SCHEDULE_START, date(2026, 11, 29), 100)
        assert fhir.list_open_slots(*args) == sqlite.list_open_slots(*args)
    finally:
        fhir_seed.load(fhir_url)  # back to the default seed for every other test
        fhir_seed.reset(fhir_url)


def test_system_clock_is_clinic_time_not_host_time() -> None:
    """Codex review, PR #16: MCP_OPS_NOW=system gave the host's (UTC) wall time, which the
    clinic (UTC-05:00) read as five hours later than it was. Both backends get this clock."""
    from mcp_ops_server.backends import clock, make_backend
    from mcp_ops_server.config import Settings

    clinic = datetime.now(UTC).astimezone(seed.CLINIC_TZ).replace(tzinfo=None)
    now = clock(Settings(now=None))
    assert abs((now() - clinic).total_seconds()) < 60
    sqlite = make_backend(Settings(now=None))
    assert abs((sqlite._now() - clinic).total_seconds()) < 60  # noqa: SLF001
    sqlite.close()
    pinned = clock(Settings(now=datetime(2026, 11, 2, 8, 0)))
    assert pinned() == datetime(2026, 11, 2, 8, 0)


@pytest.mark.fhir
def test_same_patient_booked_elsewhere_after_the_checks_is_rejected(fhir: FhirBackend) -> None:
    """Deterministic version of the same-patient race (Codex review, PR #16): between A's
    checks and A's commit, the same patient is booked into an overlapping slot with another
    provider. No slot guard can see that; the sequence If-Match must, and the re-check then
    reports the overlap."""
    mine = fhir.list_open_slots("PRV-001", seed.SCHEDULE_START, limit=100)["slots"]
    theirs = {
        s["start"]: s["slot_id"]
        for s in fhir.list_open_slots("PRV-002", seed.SCHEDULE_START, limit=100)["slots"]
    }
    slot_a, slot_b = next((s["slot_id"], theirs[s["start"]]) for s in mine if s["start"] in theirs)
    other = FhirBackend(fhir._base)  # noqa: SLF001
    real = fhir._transact  # noqa: SLF001
    raced = threading.Event()

    def transact_after_rival(entries: list[dict]) -> None:
        if not raced.is_set():
            raced.set()
            other.book_appointment(NO_REFERRAL_PATIENT, slot_b)
        real(entries)

    fhir._transact = transact_after_rival  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(ConflictError, match="overlapping"):
        fhir.book_appointment(NO_REFERRAL_PATIENT, slot_a)
    mine_now = [
        a["slot_id"] for a in fhir.snapshot_appointments().values()
        if a["patient_id"] == NO_REFERRAL_PATIENT and a["status"] == "booked"
    ]  # fmt: skip
    assert mine_now == [slot_b]
