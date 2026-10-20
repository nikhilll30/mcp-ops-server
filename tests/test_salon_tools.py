"""Unit tests for the salon's tools, scopes and integrity rules, called through the SDK."""

import asyncio
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import as_principal, call
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_ops_kit.crypto import DataKey
from salon_ops import seed
from salon_ops.auth import ALL_SCOPES
from salon_ops.server import build_server
from salon_ops.store import ConflictError, SalonStore

# Facts from the deterministic seed (pinned in test_salon_seed.py).
FREE_CUSTOMER = "CUS-0005"  # no seeded visits
SECOND_FREE = "CUS-0008"
FULL_CUSTOMER = "CUS-0006"  # already at the upcoming-visit limit
AGATE = "STY-01"  # cut, blow_dry
CORAL = "STY-03"  # colour, blow_dry
JASPER = "STY-04"  # cut, beard_trim
WEEK_TWO = "2026-11-10"
SEED = seed.generate()
EXPECTED_TOOLS = {
    "health",
    "search_stylists",
    "list_openings",
    "get_customer_visits",
    "book_visit",
    "cancel_visit",
}


@pytest.fixture(autouse=True)
def staff_principal() -> Iterator[None]:
    """Overrides the clinic conftest's: salon tests run as fully-scoped salon staff."""
    with as_principal("salon-staff", list(ALL_SCOPES)):
        yield


@pytest.fixture(params=["memory", "encrypted-file"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SalonStore]:
    """In memory, and in an encrypted file (Part 9), which must behave the same."""
    if request.param == "memory":
        s = SalonStore()
    else:
        s = SalonStore(str(tmp_path / "salon.db"), data_key=DataKey.generate())
    yield s
    s.close()


@pytest.fixture
def server(store: SalonStore) -> MCPServer:
    return build_server(store)


def call_error(server: MCPServer, tool: str, **args: object) -> str:
    with pytest.raises(ToolError) as exc:
        asyncio.run(server.call_tool(tool, args))
    return str(exc.value)


def free_openings(server: MCPServer, stylist: str, day: str = WEEK_TWO) -> list[dict]:
    return call(server, "list_openings", stylist_id=stylist, start_date=day, limit=100)["openings"]


def book(server: MCPServer, customer: str, opening: str, service: str = "cut") -> dict:
    return call(server, "book_visit", customer_id=customer, opening_id=opening, service=service)


def test_every_tool_has_input_and_output_schema(server: MCPServer) -> None:
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        assert t.description, t.name
        assert t.output_schema is not None, t.name
    by_name = {t.name: t for t in tools}
    for name in ("search_stylists", "list_openings", "get_customer_visits"):
        assert by_name[name].annotations.read_only_hint is True
    assert by_name["cancel_visit"].annotations.destructive_hint is True


# ---- search_stylists ------------------------------------------------------------------


def test_search_by_service_lists_what_each_stylist_offers(server: MCPServer) -> None:
    out = call(server, "search_stylists", service="colour")
    assert [s["stylist_id"] for s in out["stylists"]] == ["STY-02", "STY-03", "STY-06"]
    assert all("colour" in s["services"] for s in out["stylists"])


def test_search_by_studio_name_and_limit(server: MCPServer) -> None:
    assert call(server, "search_stylists", studio="old_town")["count"] == 3
    out = call(server, "search_stylists", name="  aGATE ")
    assert [(s["stylist_id"], s["services"]) for s in out["stylists"]] == [
        (AGATE, ["cut", "blow_dry"])
    ]
    assert call(server, "search_stylists", name="%")["count"] == 0  # wildcards are literal
    assert call(server, "search_stylists", limit=2)["count"] == 2


def test_search_rejects_unknown_service(server: MCPServer) -> None:
    assert "service" in call_error(server, "search_stylists", service="massage")


# ---- list_openings ----------------------------------------------------------------------


def test_openings_are_free_sorted_and_in_range(server: MCPServer) -> None:
    out = call(server, "list_openings", stylist_id=JASPER, start_date=WEEK_TWO, limit=100)
    starts = [o["start"] for o in out["openings"]]
    assert starts == sorted(starts) and starts
    assert all(WEEK_TWO <= s[:10] <= "2026-11-16" for s in starts)
    booked = {v.opening_id for v in SEED.visits}
    assert not booked & {o["opening_id"] for o in out["openings"]}
    assert out["end_date"] == "2026-11-16" and out["truncated"] is False


def test_openings_truncate_and_exclude_the_past(server: MCPServer, store: SalonStore) -> None:
    out = call(server, "list_openings", stylist_id=JASPER, start_date=WEEK_TWO, limit=2)
    assert out["count"] == 2 and out["truncated"] is True
    first = free_openings(server, JASPER)[0]
    store._now = lambda: datetime.fromisoformat(first["start"])  # noqa: SLF001
    assert first["opening_id"] not in {o["opening_id"] for o in free_openings(server, JASPER)}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"stylist_id": "STY-99", "start_date": WEEK_TWO}, "Stylist STY-99 not found"),
        ({"stylist_id": AGATE, "start_date": WEEK_TWO, "end_date": "2026-11-09"}, "on or after"),
        ({"stylist_id": AGATE, "start_date": WEEK_TWO, "end_date": "2026-11-24"}, "14 days"),
        ({"stylist_id": "sty-01", "start_date": WEEK_TWO}, "pattern"),
    ],
)
def test_openings_input_errors(server: MCPServer, args: dict, message: str) -> None:
    assert message in call_error(server, "list_openings", **args)


# ---- book_visit -------------------------------------------------------------------------


def test_book_then_the_opening_is_gone(server: MCPServer) -> None:
    opening = free_openings(server, JASPER)[0]
    visit = book(server, FREE_CUSTOMER, opening["opening_id"], "beard_trim")
    assert visit["status"] == "booked" and visit["stylist_name"] == "Jasper"
    assert visit["service"] == "beard_trim" and visit["studio"] == "old_town"
    assert visit["start"] == opening["start"] and visit["visit_id"].startswith("VIS-")
    assert opening["opening_id"] not in {o["opening_id"] for o in free_openings(server, JASPER)}
    upcoming = call(server, "get_customer_visits", customer_id=FREE_CUSTOMER)["visits"]
    assert [v["visit_id"] for v in upcoming] == [visit["visit_id"]]


def test_stylist_must_offer_the_service(server: MCPServer) -> None:
    opening = free_openings(server, AGATE)[0]["opening_id"]
    error = call_error(server, "book_visit", customer_id=FREE_CUSTOMER, opening_id=opening,
                       service="colour")  # fmt: skip
    assert "Agate (STY-01) does not offer colour; they offer cut, blow_dry" in error


def test_taken_opening_is_refused(server: MCPServer) -> None:
    opening = free_openings(server, JASPER)[0]["opening_id"]
    book(server, FREE_CUSTOMER, opening)
    error = call_error(server, "book_visit", customer_id=SECOND_FREE, opening_id=opening,
                       service="cut")  # fmt: skip
    assert f"Opening {opening} (STY-04" in error and "already booked" in error


def test_past_opening_is_refused(server: MCPServer, store: SalonStore) -> None:
    opening = free_openings(server, JASPER)[0]
    store._now = lambda: datetime.fromisoformat(opening["start"])  # noqa: SLF001
    error = call_error(server, "book_visit", customer_id=FREE_CUSTOMER,
                       opening_id=opening["opening_id"], service="cut")  # fmt: skip
    assert "in the past" in error


def test_customer_cannot_be_in_two_places(server: MCPServer) -> None:
    jasper = free_openings(server, JASPER)
    agate_at_same_time = {o["start"]: o for o in free_openings(server, AGATE)}
    clash = next(o for o in jasper if o["start"] in agate_at_same_time)
    first = book(server, FREE_CUSTOMER, clash["opening_id"])
    error = call_error(server, "book_visit", customer_id=FREE_CUSTOMER,
                       opening_id=agate_at_same_time[clash["start"]]["opening_id"],
                       service="cut")  # fmt: skip
    assert f"already has visit {first['visit_id']} at an overlapping time" in error


def test_upcoming_visit_limit(server: MCPServer) -> None:
    assert call(server, "get_customer_visits", customer_id=FULL_CUSTOMER)["count"] == 3
    opening = free_openings(server, JASPER, "2026-11-14")[-1]["opening_id"]
    error = call_error(server, "book_visit", customer_id=FULL_CUSTOMER, opening_id=opening,
                       service="cut")  # fmt: skip
    assert "already holds 3 upcoming visits, the most allowed (3)" in error


def test_past_visits_do_not_count_toward_the_limit(server: MCPServer, store: SalonStore) -> None:
    held = call(server, "get_customer_visits", customer_id=FULL_CUSTOMER)["visits"]
    store._now = lambda: datetime.fromisoformat(held[0]["start"])  # noqa: SLF001 - first is past
    late = free_openings(server, JASPER, "2026-11-14")[-1]["opening_id"]
    assert book(server, FULL_CUSTOMER, late)["status"] == "booked"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"customer_id": "CUS-9999", "opening_id": "OPN-00001", "service": "cut"}, "Customer"),
        ({"customer_id": FREE_CUSTOMER, "opening_id": "OPN-99999", "service": "cut"}, "Opening"),
        ({"customer_id": FREE_CUSTOMER, "opening_id": "OPN-00001"}, "service"),
        ({"customer_id": "CUS-1", "opening_id": "OPN-00001", "service": "cut"}, "pattern"),
    ],
)
def test_book_input_errors(server: MCPServer, args: dict, message: str) -> None:
    assert message in call_error(server, "book_visit", **args)


def test_concurrent_bookings_of_one_opening_yield_one_winner(store: SalonStore) -> None:
    opening = store.list_openings(JASPER, datetime(2026, 11, 10).date())["openings"][0]
    customers = [c for c in SEED.customer_ids if c not in {v.customer_id for v in SEED.visits}]
    barrier = threading.Barrier(len(customers))

    def attempt(customer: str) -> str:
        barrier.wait()
        try:
            store.book_visit(customer, opening["opening_id"], "cut")
            return "won"
        except ConflictError:
            return "lost"

    with ThreadPoolExecutor(len(customers)) as pool:
        outcomes = list(pool.map(attempt, customers))
    assert outcomes.count("won") == 1
    booked = [
        v for v in store.snapshot_visits().values() if v["opening_id"] == opening["opening_id"]
    ]
    assert [v["status"] for v in booked] == ["booked"]


def test_unique_index_blocks_double_booking_even_without_app_check(store: SalonStore) -> None:
    import sqlite3

    taken = SEED.visits[0].opening_id
    with pytest.raises(sqlite3.IntegrityError), store._write() as c:  # noqa: SLF001
        c.execute(
            "INSERT INTO visits (opening_id, customer_id, service, status) "
            "VALUES (?, ?, 'cut', 'booked')",
            (taken, FREE_CUSTOMER),
        )


# ---- cancel_visit -----------------------------------------------------------------------


def test_cancel_frees_the_opening(server: MCPServer) -> None:
    opening = free_openings(server, JASPER)[0]["opening_id"]
    visit = book(server, FREE_CUSTOMER, opening)
    out = call(server, "cancel_visit", visit_id=visit["visit_id"], customer_id=FREE_CUSTOMER)
    assert out["status"] == "cancelled"
    assert opening in {o["opening_id"] for o in free_openings(server, JASPER)}
    assert call(server, "get_customer_visits", customer_id=FREE_CUSTOMER)["count"] == 0
    again = call_error(
        server, "cancel_visit", visit_id=visit["visit_id"], customer_id=FREE_CUSTOMER
    )
    assert "already cancelled" in again


def test_late_cancellation_boundary(server: MCPServer, store: SalonStore) -> None:
    visit = book(server, FREE_CUSTOMER, free_openings(server, JASPER)[0]["opening_id"])
    start = datetime.fromisoformat(visit["start"])
    store._now = lambda: start - timedelta(hours=24)  # noqa: SLF001 - exactly 24 h: too late
    error = call_error(
        server, "cancel_visit", visit_id=visit["visit_id"], customer_id=FREE_CUSTOMER
    )
    assert "less than 24 hours from now" in error and "call the studio" in error
    store._now = lambda: start - timedelta(hours=24, seconds=1)  # noqa: SLF001
    out = call(server, "cancel_visit", visit_id=visit["visit_id"], customer_id=FREE_CUSTOMER)
    assert out["status"] == "cancelled"


def test_cancel_someone_elses_visit_looks_like_not_found(server: MCPServer) -> None:
    visit = book(server, FREE_CUSTOMER, free_openings(server, JASPER)[0]["opening_id"])
    other = call_error(server, "cancel_visit", visit_id=visit["visit_id"], customer_id=SECOND_FREE)
    unknown = call_error(server, "cancel_visit", visit_id="VIS-99999", customer_id=SECOND_FREE)
    assert other.replace(visit["visit_id"], "X") == unknown.replace("VIS-99999", "X")


def test_get_customer_visits_lists_upcoming_only(server: MCPServer, store: SalonStore) -> None:
    held = call(server, "get_customer_visits", customer_id=FULL_CUSTOMER)["visits"]
    assert [v["start"] for v in held] == sorted(v["start"] for v in held)
    store._now = lambda: datetime.fromisoformat(held[0]["start"])  # noqa: SLF001
    later = call(server, "get_customer_visits", customer_id=FULL_CUSTOMER)["visits"]
    assert [v["visit_id"] for v in later] == [v["visit_id"] for v in held[1:]]
    assert "Customer CUS-9999 not found" in call_error(
        server, "get_customer_visits", customer_id="CUS-9999"
    )


# ---- scopes and customer binding (enforced in server code) ------------------------------

SCOPED = {
    "search_stylists": ({}, "read:catalog"),
    "list_openings": ({"stylist_id": AGATE, "start_date": WEEK_TWO}, "read:catalog"),
    "get_customer_visits": ({"customer_id": FREE_CUSTOMER}, "read:customers"),
    "book_visit": (
        {"customer_id": FREE_CUSTOMER, "opening_id": "OPN-00001", "service": "cut"},
        "write:bookings",
    ),
    "cancel_visit": ({"visit_id": "VIS-00001", "customer_id": FREE_CUSTOMER}, "write:bookings"),
}


@pytest.mark.parametrize("tool", sorted(SCOPED))
def test_every_tool_refuses_unauthenticated_calls(server: MCPServer, tool: str) -> None:
    reset = auth_context_var.set(None)
    try:
        assert "Unauthenticated" in call_error(server, tool, **SCOPED[tool][0])
    finally:
        auth_context_var.reset(reset)


@pytest.mark.parametrize("tool", sorted(SCOPED))
def test_every_tool_requires_its_scope(server: MCPServer, tool: str) -> None:
    args, scope = SCOPED[tool]
    with as_principal("salon-limited", [s for s in ALL_SCOPES if s != scope]):
        assert f"lacks the {scope} scope" in call_error(server, tool, **args)


@contextmanager
def as_customer(customer_id: str) -> Iterator[None]:
    """A customer-bound app token, as the HTTP auth middleware would set it."""
    token = AccessToken(
        token="test-token",  # noqa: S106 - not a credential; never checked in-process
        client_id="salon-app",
        scopes=list(ALL_SCOPES),
        claims={"customer_id": customer_id},
    )
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


@pytest.mark.parametrize("tool", ["get_customer_visits", "book_visit", "cancel_visit"])
def test_customer_bound_token_cannot_act_for_others(server: MCPServer, tool: str) -> None:
    with as_customer(SECOND_FREE):
        error = call_error(server, tool, **SCOPED[tool][0])
    assert f"may only act for customer {SECOND_FREE}" in error


def test_customer_bound_token_can_act_for_itself_and_browse(server: MCPServer) -> None:
    with as_customer(FREE_CUSTOMER):
        opening = free_openings(server, JASPER)[0]["opening_id"]
        assert book(server, FREE_CUSTOMER, opening)["customer_id"] == FREE_CUSTOMER
        assert call(server, "search_stylists")["count"] == 6


# ---- Codex review, PR #18: the clock is read after the lock, not before ------------------


def _while_locked(store: SalonStore, advance_to: datetime, call: object) -> object:
    """Run ``call`` while another caller holds the store's lock; move the clock to
    ``advance_to`` before releasing it, as time passes for a queued write."""
    store._lock.acquire()  # noqa: SLF001 - simulate a slow write ahead of this one
    out: list[object] = []

    def run() -> None:
        try:
            out.append(call())
        except Exception as exc:  # noqa: BLE001 - the outcome is what the test checks
            out.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.2)  # the call is now queued on the lock
    store._now = lambda: advance_to  # noqa: SLF001
    store._lock.release()  # noqa: SLF001
    worker.join(timeout=5)
    return out[0]


def test_a_queued_cancel_checks_the_window_at_commit_time(store: SalonStore) -> None:
    opening = store.list_openings(JASPER, datetime(2026, 11, 10).date())["openings"][0]
    visit = store.book_visit(FREE_CUSTOMER, opening["opening_id"], "cut")
    start = datetime.fromisoformat(visit["start"])
    store._now = lambda: start - timedelta(hours=25)  # noqa: SLF001 - cancellable when called
    outcome = _while_locked(
        store,
        start - timedelta(hours=23),  # inside the window by the time it runs
        lambda: store.cancel_visit(visit["visit_id"], FREE_CUSTOMER),
    )
    assert isinstance(outcome, ConflictError) and "less than 24 hours" in str(outcome)


def test_a_queued_booking_checks_the_start_at_commit_time(store: SalonStore) -> None:
    opening = store.list_openings(JASPER, datetime(2026, 11, 10).date())["openings"][0]
    start = datetime.fromisoformat(opening["start"])
    outcome = _while_locked(
        store,
        start + timedelta(minutes=5),  # the opening has started by the time it runs
        lambda: store.book_visit(FREE_CUSTOMER, opening["opening_id"], "cut"),
    )
    assert isinstance(outcome, ConflictError) and "in the past" in str(outcome)
