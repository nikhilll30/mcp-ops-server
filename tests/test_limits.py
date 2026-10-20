"""Rate limiter, per-call time limits, simulated clock, and settings."""

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
from conftest import NO_REFERRAL_PATIENT, PRIMARY_PROVIDER, seeded_busy_starts

from mcp_ops_kit.limits import RateLimiter
from mcp_ops_server import seed
from mcp_ops_server.backends.base import BusyError, CallTimeoutError, ConflictError
from mcp_ops_server.backends.sqlite import SqliteBackend
from mcp_ops_server.config import Settings

# ---- rate limiter --------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_bucket_allows_burst_then_refills() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=60, clock=clock)  # one call per second
    assert all(limiter.acquire("a") is None for _ in range(60))
    retry = limiter.acquire("a")
    assert retry is not None and 0.9 < retry <= 1.0
    clock.t += 1.0
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is not None


def test_bucket_never_exceeds_capacity() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=5, clock=clock)
    clock.t += 3600  # idle for an hour
    assert sum(limiter.acquire("a") is None for _ in range(10)) == 5


def test_buckets_are_independent() -> None:
    limiter = RateLimiter(per_minute=1, clock=FakeClock())
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is not None
    assert limiter.acquire("b") is None


def test_rate_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RateLimiter(per_minute=0)


# ---- per-call time limits ------------------------------------------------------------------

SLOW_QUERY = (
    "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 50000000) "
    "SELECT COUNT(*) FROM n"
)


def test_runaway_read_is_interrupted_and_store_recovers() -> None:
    store = SqliteBackend(timeout_s=0.2)
    with pytest.raises(CallTimeoutError):
        store._read(SLOW_QUERY)  # noqa: SLF001 - no public tool is slow enough to trip it
    assert store.search_providers()["count"] == 12


def test_runaway_write_is_rolled_back() -> None:
    store = SqliteBackend(timeout_s=0.2)
    slot_id = _open_slot(store)
    with pytest.raises(CallTimeoutError), store._write() as c:  # noqa: SLF001
        c.execute(
            "INSERT INTO appointments (slot_id, patient_id, status) VALUES (?, ?, 'booked')",
            (slot_id, NO_REFERRAL_PATIENT),
        )
        c.execute(SLOW_QUERY).fetchall()
    assert not store._conn.in_transaction  # noqa: SLF001
    assert _open_slot(store) == slot_id, "the timed-out insert must not have landed"


def test_waiting_for_the_lock_is_bounded() -> None:
    store = SqliteBackend(timeout_s=0.1)
    store._lock.acquire()  # noqa: SLF001 - simulate a stuck caller holding the lock
    try:
        with pytest.raises(BusyError):
            store.search_providers()
    finally:
        store._lock.release()  # noqa: SLF001


# ---- simulated clock -----------------------------------------------------------------------


def _at(when: str) -> SqliteBackend:
    return SqliteBackend(now=lambda: datetime.fromisoformat(when))


def test_past_slots_are_hidden() -> None:
    store = _at("2026-11-03T12:00:00")
    out = store.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START, limit=100)
    assert out["slots"] and all(s["start"] > "2026-11-03T12:00:00" for s in out["slots"])


def test_booking_a_past_slot_is_refused() -> None:
    early = _at("2026-11-02T08:00:00")
    slot_id = _open_slot(early)
    late = _at("2026-11-30T00:00:00")
    with pytest.raises(ConflictError, match="in the past"):
        late.book_appointment(NO_REFERRAL_PATIENT, slot_id)


def test_started_appointment_cannot_be_cancelled() -> None:
    now = {"t": datetime(2026, 11, 2, 8, 0)}
    store = SqliteBackend(now=lambda: now["t"])
    slot_id = _open_slot(store)
    appt = store.book_appointment(NO_REFERRAL_PATIENT, slot_id)
    now["t"] = datetime.fromisoformat(appt["start"])
    with pytest.raises(ConflictError, match="can no longer be cancelled"):
        store.cancel_appointment(appt["appointment_id"], NO_REFERRAL_PATIENT)


def test_default_clock_is_the_simulated_monday_morning() -> None:
    assert Settings().now == seed.CLINIC_NOW == datetime(2026, 11, 2, 8, 0)
    assert seed.CLINIC_NOW < min(s.start for s in seed.generate().slots)


# ---- settings ------------------------------------------------------------------------------


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_OPS_PORT", "9001")
    monkeypatch.setenv("MCP_OPS_TOKENS_FILE", "t.json")
    monkeypatch.setenv("MCP_OPS_RATE_LIMIT_PER_MIN", "7")
    monkeypatch.setenv("MCP_OPS_MAX_BODY_BYTES", "1024")
    monkeypatch.setenv("MCP_OPS_NOW", "2026-11-10T09:00:00")
    s = Settings.from_env()
    assert (s.port, s.tokens_file, s.rate_limit_per_minute, s.max_body_bytes) == (
        9001,
        "t.json",
        7,
        1024,
    )
    assert s.now == datetime(2026, 11, 10, 9, 0)


def test_settings_system_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_OPS_NOW", "system")
    assert Settings.from_env().now is None


def _open_slot(store: SqliteBackend) -> str:
    out = store.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START, limit=100)
    busy = seeded_busy_starts(NO_REFERRAL_PATIENT)
    return next(s["slot_id"] for s in out["slots"] if s["start"] not in busy)


# ---- the deadline covers the whole call, including waits (Codex review, Part 3) ------------

SLACK_S = 0.08  # scheduling noise allowed beyond the deadline


def _file_store(tmp_path: Path, timeout_s: float) -> SqliteBackend:
    return SqliteBackend(str(tmp_path / "clinic.db"), timeout_s=timeout_s)


def test_lock_wait_and_db_wait_share_one_deadline(tmp_path: Path) -> None:
    """In-process lock contention, then another connection's write lock: 150 ms + 150 ms of
    waiting against a 200 ms budget must fail at ~200 ms, not succeed at ~300 ms."""
    store = _file_store(tmp_path, timeout_s=0.2)
    slot_id = _open_slot(store)
    other = sqlite3.connect(
        str(tmp_path / "clinic.db"), isolation_level=None, check_same_thread=False
    )
    holding = threading.Event()

    def contend() -> None:
        store._lock.acquire()  # noqa: SLF001 - simulate a slow in-process caller
        holding.set()
        time.sleep(0.15)
        other.execute("BEGIN IMMEDIATE")  # then another process holds the write lock
        store._lock.release()  # noqa: SLF001
        time.sleep(0.15)
        other.execute("ROLLBACK")

    t = threading.Thread(target=contend)
    t.start()
    holding.wait()
    start = time.monotonic()
    with pytest.raises((BusyError, CallTimeoutError)):
        store.book_appointment(NO_REFERRAL_PATIENT, slot_id)
    elapsed = time.monotonic() - start
    t.join()
    assert elapsed < 0.2 + SLACK_S, f"call took {elapsed * 1000:.0f} ms"
    assert _open_slot(store) == slot_id


def test_write_past_deadline_is_rolled_back_not_committed(tmp_path: Path) -> None:
    store = _file_store(tmp_path, timeout_s=0.2)
    slot_id = _open_slot(store)
    with pytest.raises(CallTimeoutError), store._write() as c:  # noqa: SLF001
        c.execute(
            "INSERT INTO appointments (slot_id, patient_id, status) VALUES (?, ?, 'booked')",
            (slot_id, NO_REFERRAL_PATIENT),
        )
        time.sleep(0.25)  # the work itself overran the budget
    assert _open_slot(store) == slot_id


def test_commit_blocked_by_a_reader_gives_up_within_the_deadline(tmp_path: Path) -> None:
    store = _file_store(tmp_path, timeout_s=0.2)
    slot_id = _open_slot(store)
    reader = sqlite3.connect(str(tmp_path / "clinic.db"), isolation_level=None)
    start = time.monotonic()
    with pytest.raises(BusyError), store._write() as c:  # noqa: SLF001
        c.execute(
            "INSERT INTO appointments (slot_id, patient_id, status) VALUES (?, ?, 'booked')",
            (slot_id, NO_REFERRAL_PATIENT),
        )
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM slots LIMIT 1").fetchall()  # blocks our COMMIT
    elapsed = time.monotonic() - start
    reader.execute("ROLLBACK")
    assert elapsed < 0.2 + SLACK_S, f"call took {elapsed * 1000:.0f} ms"
    assert _open_slot(store) == slot_id
