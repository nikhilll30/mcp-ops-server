"""Store behaviour not visible through a single tool call."""

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import NO_REFERRAL_PATIENT, PRIMARY_PROVIDER, call, seeded_busy_starts
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from mcp_ops_server import seed
from mcp_ops_server.server import build_server
from mcp_ops_server.store import BusyError, ConflictError, NotFoundError, Store


@pytest.fixture
def server_and_store(store: Store) -> tuple:
    return build_server(store), store


def test_file_store_persists_bookings_and_is_not_reseeded(tmp_path: Path) -> None:
    path = str(tmp_path / "clinic.db")
    first = Store(path)
    slot_id = first.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"][0]["slot_id"]
    booked = first.book_appointment(NO_REFERRAL_PATIENT, slot_id)
    first.close()

    reopened = Store(path)
    open_ids = {
        s["slot_id"]
        for s in reopened.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"]
    }
    assert slot_id not in open_ids
    cancelled = reopened.cancel_appointment(booked["appointment_id"], NO_REFERRAL_PATIENT)
    assert cancelled["status"] == "cancelled"
    reopened.close()


def test_failed_write_rolls_back(store: Store) -> None:
    """An error mid-transaction must not leave the connection inside an open transaction."""
    with pytest.raises(NotFoundError):
        store.book_appointment("PAT-0999", "SLT-00001")
    slot_id = store.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"][0]["slot_id"]
    assert store.book_appointment(NO_REFERRAL_PATIENT, slot_id)["status"] == "booked"


# ---- transactions under real contention (separate connections to one file) ---------------


def test_failed_commit_rolls_back_and_store_stays_writable(tmp_path: Path) -> None:
    """COMMIT fails while another connection holds a read lock; the store must recover."""
    path = str(tmp_path / "clinic.db")
    store = Store(path, timeout_s=0.2)
    slot_id = _open_slot(store)
    reader = sqlite3.connect(path, isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM slots LIMIT 1").fetchall()  # holds a SHARED lock
    with pytest.raises(BusyError):
        store.book_appointment(NO_REFERRAL_PATIENT, slot_id)
    reader.execute("ROLLBACK")
    reader.close()
    assert not store._conn.in_transaction  # noqa: SLF001
    assert slot_id == _open_slot(store), "rolled-back booking must not hold the slot"
    assert store.book_appointment(NO_REFERRAL_PATIENT, slot_id)["status"] == "booked"
    store.close()


def test_concurrent_first_start_seeds_once(tmp_path: Path) -> None:
    path = str(tmp_path / "clinic.db")
    n = 6
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def open_store() -> None:
        barrier.wait()
        try:
            Store(path).close()
        except BaseException as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=open_store) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM providers").fetchone() == (12,)
    assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone() == (
        len(seed.generate().appointments),
    )
    conn.close()


def _race(path: str, attempts: list[tuple[str, str]]) -> list[str]:
    """Each attempt books through its own Store (own connection) at the same moment."""
    stores = [Store(path) for _ in attempts]
    barrier = threading.Barrier(len(attempts))

    def attempt(store: Store, patient_id: str, slot_id: str) -> str:
        barrier.wait()
        try:
            store.book_appointment(patient_id, slot_id)
            return "ok"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(len(attempts)) as pool:
        futures = [
            pool.submit(attempt, store, patient_id, slot_id)
            for store, (patient_id, slot_id) in zip(stores, attempts, strict=True)
        ]
        results = [f.result() for f in futures]
    for s in stores:
        s.close()
    return results


def test_separate_connections_race_for_one_slot(tmp_path: Path) -> None:
    path = str(tmp_path / "clinic.db")
    setup = Store(path)
    slot = setup.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START, limit=1)["slots"][0]
    setup.close()
    free = [p for p in seed.generate().patient_ids if slot["start"] not in seeded_busy_starts(p)]
    results = _race(path, [(p, slot["slot_id"]) for p in free[:8]])
    assert sorted(results) == ["conflict"] * 7 + ["ok"]


def test_separate_connections_race_one_patient_into_overlapping_slots(tmp_path: Path) -> None:
    """Different slots (no unique-index protection), same patient, same time: one must win."""
    path = str(tmp_path / "clinic.db")
    setup = Store(path)
    no_referral = ("PRV-001", "PRV-002", "PRV-007", "PRV-008")
    by_start: dict[str, list[str]] = {}
    for provider_id in no_referral:
        for s in setup.list_open_slots(provider_id, seed.SCHEDULE_START, limit=100)["slots"]:
            by_start.setdefault(s["start"], []).append(s["slot_id"])
    setup.close()
    slots = next(ids for ids in by_start.values() if len(ids) == len(no_referral))
    results = _race(path, [(NO_REFERRAL_PATIENT, slot_id) for slot_id in slots])
    assert sorted(results) == ["conflict"] * (len(slots) - 1) + ["ok"]


# ---- overlap boundaries -------------------------------------------------------------------


def _add_slot(store: Store, slot_id: str, provider_id: str, start: str, end: str) -> None:
    store._conn.execute(  # noqa: SLF001 - seed has only aligned slots; add an unaligned one
        "INSERT INTO slots VALUES (?, ?, ?, ?)", (slot_id, provider_id, start, end)
    )


def test_partial_overlap_is_rejected(store: Store) -> None:
    _add_slot(store, "SLT-90001", "PRV-002", "2026-11-30T09:00:00", "2026-11-30T09:30:00")
    _add_slot(store, "SLT-90002", "PRV-007", "2026-11-30T09:15:00", "2026-11-30T09:45:00")
    store.book_appointment(NO_REFERRAL_PATIENT, "SLT-90001")
    with pytest.raises(ConflictError, match="overlapping"):
        store.book_appointment(NO_REFERRAL_PATIENT, "SLT-90002")


def test_back_to_back_slots_are_allowed(store: Store) -> None:
    _add_slot(store, "SLT-90001", "PRV-002", "2026-11-30T09:00:00", "2026-11-30T09:30:00")
    _add_slot(store, "SLT-90002", "PRV-007", "2026-11-30T09:30:00", "2026-11-30T10:00:00")
    store.book_appointment(NO_REFERRAL_PATIENT, "SLT-90001")
    assert store.book_appointment(NO_REFERRAL_PATIENT, "SLT-90002")["status"] == "booked"


# ---- IDs and dates --------------------------------------------------------------------------


def test_six_digit_appointment_ids_round_trip(server_and_store: tuple) -> None:
    server, store = server_and_store
    store._conn.execute(  # noqa: SLF001 - fast-forward the ID sequence
        "UPDATE sqlite_sequence SET seq = 99999 WHERE name = 'appointments'"
    )
    slot_id = _open_slot(store)
    appt = call(server, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot_id)
    assert appt["appointment_id"] == "APT-100000"
    out = call(
        server,
        "cancel_appointment",
        appointment_id="APT-100000",
        patient_id=NO_REFERRAL_PATIENT,
    )
    assert out["status"] == "cancelled"


@pytest.mark.parametrize(
    "dates",
    [
        {"start_date": "9999-12-31"},
        {"start_date": "9999-12-31", "end_date": "9999-12-31"},
        {"start_date": "0001-01-01"},
    ],
)
def test_extreme_dates_get_a_clear_error(server_and_store: tuple, dates: dict) -> None:
    server, _ = server_and_store
    with pytest.raises(ToolError, match="Dates must be between") as exc:
        asyncio.run(server.call_tool("list_open_slots", {"provider_id": "PRV-001", **dates}))
    assert not isinstance(exc.value, UnexpectedToolError)


def _open_slot(store: Store) -> str:
    out = store.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START, limit=100)
    busy = seeded_busy_starts(NO_REFERRAL_PATIENT)
    return next(s["slot_id"] for s in out["slots"] if s["start"] not in busy)
