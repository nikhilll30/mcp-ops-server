"""Rate limiter, per-call time limits, simulated clock, and settings."""

from datetime import datetime

import pytest
from conftest import NO_REFERRAL_PATIENT, PRIMARY_PROVIDER, seeded_busy_starts

from mcp_ops_server import seed
from mcp_ops_server.config import Settings
from mcp_ops_server.limits import RateLimiter
from mcp_ops_server.store import BusyError, CallTimeoutError, ConflictError, Store

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
    store = Store(timeout_s=0.2)
    with pytest.raises(CallTimeoutError):
        store._read(SLOW_QUERY)  # noqa: SLF001 - no public tool is slow enough to trip it
    assert store.search_providers()["count"] == 12


def test_runaway_write_is_rolled_back() -> None:
    store = Store(timeout_s=0.2)
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
    store = Store(timeout_s=0.1)
    store._lock.acquire()  # noqa: SLF001 - simulate a stuck caller holding the lock
    try:
        with pytest.raises(BusyError):
            store.search_providers()
    finally:
        store._lock.release()  # noqa: SLF001


# ---- simulated clock -----------------------------------------------------------------------


def _at(when: str) -> Store:
    return Store(now=lambda: datetime.fromisoformat(when))


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
    store = Store(now=lambda: now["t"])
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


def _open_slot(store: Store) -> str:
    out = store.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START, limit=100)
    busy = seeded_busy_starts(NO_REFERRAL_PATIENT)
    return next(s["slot_id"] for s in out["slots"] if s["start"] not in busy)
