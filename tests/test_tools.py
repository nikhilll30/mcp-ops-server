"""Unit tests for the five clinic tools, called through the SDK so schemas are exercised."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import (
    CARDIO_PROVIDER,
    DENIED_NEURO,
    DERM_PATIENT,
    DERM_PROVIDER,
    DERM_REFERRAL,
    NO_REFERRAL_PATIENT,
    OTHER_PATIENTS_REFERRAL,
    PENDING_CARDIO,
    PRIMARY_PROVIDER,
    SECOND_FREE_PATIENT,
    WEEK_ONE,
    call,
    first_open_slot,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_ops_server import seed
from mcp_ops_server.server import build_server
from mcp_ops_server.store import ConflictError, Store

EXPECTED_TOOLS = {
    "health",
    "search_providers",
    "list_open_slots",
    "book_appointment",
    "cancel_appointment",
    "get_referral_status",
}


def call_error(server: MCPServer, tool: str, **args: object) -> str:
    with pytest.raises(ToolError) as exc:
        asyncio.run(server.call_tool(tool, args))
    return str(exc.value)


def test_every_tool_has_input_and_output_schema(server: MCPServer) -> None:
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        assert t.description, t.name
        assert t.output_schema is not None, t.name


def test_read_only_tools_are_annotated(server: MCPServer) -> None:
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    for name in ("search_providers", "list_open_slots", "get_referral_status"):
        assert tools[name].annotations.read_only_hint is True
    assert tools["cancel_appointment"].annotations.destructive_hint is True


# ---- search_providers ------------------------------------------------------------------


def test_search_by_specialty(server: MCPServer) -> None:
    out = call(server, "search_providers", specialty="cardiology")
    assert out["count"] == 2
    assert all(p["specialty"] == "cardiology" for p in out["providers"])
    assert all(p["requires_referral"] for p in out["providers"])


def test_search_primary_care_needs_no_referral(server: MCPServer) -> None:
    out = call(server, "search_providers", specialty="primary_care")
    assert [p["requires_referral"] for p in out["providers"]] == [False, False]


def test_search_by_name_is_case_insensitive(server: MCPServer) -> None:
    out = call(server, "search_providers", name="  dogWOOD ")
    assert [p["provider_id"] for p in out["providers"]] == [DERM_PROVIDER]


def test_search_name_wildcards_are_literal(server: MCPServer) -> None:
    assert call(server, "search_providers", name="%")["count"] == 0
    assert call(server, "search_providers", name="_")["count"] == 0


def test_search_combined_filters_and_limit(server: MCPServer) -> None:
    out = call(server, "search_providers", location="downtown", specialty="neurology")
    assert {p["location"] for p in out["providers"]} == {"downtown"}
    assert call(server, "search_providers", limit=3)["count"] == 3
    assert call(server, "search_providers")["count"] == 12


def test_search_rejects_unknown_specialty(server: MCPServer) -> None:
    assert "specialty" in call_error(server, "search_providers", specialty="astrology")


# ---- list_open_slots -------------------------------------------------------------------


def test_open_slots_are_sorted_and_in_range(server: MCPServer) -> None:
    out = call(
        server,
        "list_open_slots",
        provider_id=PRIMARY_PROVIDER,
        start_date=WEEK_ONE,
        end_date="2026-11-03",
        limit=100,
    )
    starts = [s["start"] for s in out["slots"]]
    assert starts == sorted(starts)
    assert all("2026-11-02" <= s[:10] <= "2026-11-03" for s in starts)
    assert out["truncated"] is False


def test_open_slots_exclude_prebooked(server: MCPServer) -> None:
    booked = seed.generate().appointments[0].slot_id
    slot = next(s for s in seed.generate().slots if s.id == booked)
    out = call(
        server,
        "list_open_slots",
        provider_id=slot.provider_id,
        start_date=slot.start.date().isoformat(),
        end_date=slot.start.date().isoformat(),
        limit=100,
    )
    assert booked not in {s["slot_id"] for s in out["slots"]}


def test_open_slots_truncation(server: MCPServer) -> None:
    out = call(
        server, "list_open_slots", provider_id=PRIMARY_PROVIDER, start_date=WEEK_ONE, limit=2
    )
    assert out["count"] == 2 and out["truncated"] is True


def test_open_slots_default_window_is_one_week(server: MCPServer) -> None:
    out = call(server, "list_open_slots", provider_id=PRIMARY_PROVIDER, start_date=WEEK_ONE)
    assert out["end_date"] == "2026-11-08"


def test_open_slots_outside_schedule_is_empty(server: MCPServer) -> None:
    out = call(server, "list_open_slots", provider_id=PRIMARY_PROVIDER, start_date="2027-01-04")
    assert out["slots"] == [] and out["count"] == 0


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"provider_id": "PRV-999", "start_date": WEEK_ONE}, "not found"),
        (
            {"provider_id": "PRV-001", "start_date": "2026-11-09", "end_date": WEEK_ONE},
            "on or after",
        ),
        ({"provider_id": "PRV-001", "start_date": WEEK_ONE, "end_date": "2026-12-31"}, "31 days"),
        ({"provider_id": "doctor-1", "start_date": WEEK_ONE}, "provider_id"),
        ({"provider_id": "PRV-001", "start_date": "next tuesday"}, "start_date"),
    ],
)
def test_open_slots_input_errors(server: MCPServer, args: dict, message: str) -> None:
    assert message in call_error(server, "list_open_slots", **args)


# ---- book_appointment ------------------------------------------------------------------


def test_book_without_referral_requirement(server: MCPServer) -> None:
    slot_id = first_open_slot(server, PRIMARY_PROVIDER, NO_REFERRAL_PATIENT)
    out = call(server, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot_id)
    assert out["status"] == "booked"
    assert out["appointment_id"].startswith("APT-")
    assert (out["slot_id"], out["provider_id"], out["referral_id"]) == (
        slot_id,
        PRIMARY_PROVIDER,
        None,
    )
    reopened = call(server, "list_open_slots", provider_id=PRIMARY_PROVIDER, start_date=WEEK_ONE)
    assert slot_id not in {s["slot_id"] for s in reopened["slots"]}


def test_book_with_approved_referral(server: MCPServer) -> None:
    slot_id = first_open_slot(server, DERM_PROVIDER, DERM_PATIENT)
    out = call(
        server,
        "book_appointment",
        patient_id=DERM_PATIENT,
        slot_id=slot_id,
        referral_id=DERM_REFERRAL,
    )
    assert out["status"] == "booked" and out["referral_id"] == DERM_REFERRAL
    assert out["specialty"] == "dermatology"


def test_double_booking_rejected(server: MCPServer) -> None:
    slot_id = first_open_slot(server, PRIMARY_PROVIDER, NO_REFERRAL_PATIENT)
    call(server, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot_id)
    msg = call_error(server, "book_appointment", patient_id=SECOND_FREE_PATIENT, slot_id=slot_id)
    assert "already booked" in msg


def test_prebooked_slot_rejected(server: MCPServer) -> None:
    data = seed.generate()
    booked = next(a for a in data.appointments if a.referral_id is None)
    slot = next(s for s in data.slots if s.id == booked.slot_id)
    msg = call_error(server, "book_appointment", patient_id="PAT-0040", slot_id=booked.slot_id)
    # Names the provider and time, so the caller can look for another slot that day.
    assert "already booked" in msg
    assert slot.provider_id in msg and slot.start.isoformat() in msg


def test_patient_cannot_be_in_two_places(server: MCPServer) -> None:
    slot_a = first_open_slot(server, PRIMARY_PROVIDER, NO_REFERRAL_PATIENT)
    appt = call(server, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot_a)
    # Same start time with another no-referral provider.
    slot_b = next(
        s["slot_id"]
        for provider_id in ("PRV-002", "PRV-007", "PRV-008")
        for s in call(
            server, "list_open_slots", provider_id=provider_id, start_date=appt["start"][:10]
        )["slots"]
        if s["start"] == appt["start"]
    )
    msg = call_error(server, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot_b)
    assert "overlapping" in msg


@pytest.mark.parametrize(
    ("patient_id", "referral_id", "provider_id", "message"),
    [
        (NO_REFERRAL_PATIENT, None, DERM_PROVIDER, "requires an approved referral"),
        (*PENDING_CARDIO, CARDIO_PROVIDER, "is pending"),
        (DERM_PATIENT, OTHER_PATIENTS_REFERRAL, DERM_PROVIDER, "not found for patient"),
        (DERM_PATIENT, DERM_REFERRAL, CARDIO_PROVIDER, "is for dermatology"),
        (DERM_PATIENT, "REF-9999", DERM_PROVIDER, "not found for patient"),
    ],
)
def test_referral_rules(
    server: MCPServer, patient_id: str, referral_id: str | None, provider_id: str, message: str
) -> None:
    slot_id = first_open_slot(server, provider_id, patient_id)
    args = {"patient_id": patient_id, "slot_id": slot_id}
    if referral_id:
        args["referral_id"] = referral_id
    assert message in call_error(server, "book_appointment", **args)
    # A rejected booking leaves the slot open.
    assert first_open_slot(server, provider_id, patient_id) == slot_id


def test_denied_referral_rejected(server: MCPServer) -> None:
    patient_id, referral_id = DENIED_NEURO
    slot_id = first_open_slot(server, "PRV-006", patient_id)
    msg = call_error(
        server, "book_appointment", patient_id=patient_id, slot_id=slot_id, referral_id=referral_id
    )
    assert "is denied" in msg


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"patient_id": "PAT-0999", "slot_id": "SLT-00001"}, "Patient PAT-0999 not found"),
        ({"patient_id": "PAT-0001", "slot_id": "SLT-99999"}, "Slot SLT-99999 not found"),
        ({"patient_id": "Jane", "slot_id": "SLT-00001"}, "patient_id"),
        ({"patient_id": "PAT-0001"}, "slot_id"),
    ],
)
def test_book_input_errors(server: MCPServer, args: dict, message: str) -> None:
    assert message in call_error(server, "book_appointment", **args)


def test_concurrent_bookings_of_one_slot_yield_one_winner(store: Store) -> None:
    """Many threads race for one slot; the lock + unique index must allow exactly one."""
    slot_id = store.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"][0]["slot_id"]
    patients = [f"PAT-{i:04d}" for i in range(11, 31)]
    barrier = threading.Barrier(len(patients))

    def attempt(patient_id: str) -> str:
        barrier.wait()
        try:
            store.book_appointment(patient_id, slot_id)
            return "ok"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(len(patients)) as pool:
        results = list(pool.map(attempt, patients))
    assert results.count("ok") == 1
    assert results.count("conflict") == len(patients) - 1


def test_unique_index_blocks_double_booking_even_without_app_check(store: Store) -> None:
    conn = store._conn  # noqa: SLF001 - deliberately bypass the store's checks
    slot = seed.generate().appointments[0].slot_id
    with pytest.raises(Exception, match="UNIQUE"):
        conn.execute(
            "INSERT INTO appointments (slot_id, patient_id, status) "
            "VALUES (?, 'PAT-0040', 'booked')",
            (slot,),
        )


# ---- cancel_appointment ----------------------------------------------------------------


def test_cancel_reopens_slot_and_allows_rebooking(server: MCPServer) -> None:
    slot_id = first_open_slot(server, PRIMARY_PROVIDER, NO_REFERRAL_PATIENT)
    appt = call(server, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot_id)
    out = call(
        server,
        "cancel_appointment",
        appointment_id=appt["appointment_id"],
        patient_id=NO_REFERRAL_PATIENT,
    )
    assert out["status"] == "cancelled" and out["slot_id"] == slot_id
    assert first_open_slot(server, PRIMARY_PROVIDER, NO_REFERRAL_PATIENT) == slot_id
    again = call(server, "book_appointment", patient_id=SECOND_FREE_PATIENT, slot_id=slot_id)
    assert again["appointment_id"] != appt["appointment_id"]


def test_cancel_twice_rejected(server: MCPServer) -> None:
    slot_id = first_open_slot(server, PRIMARY_PROVIDER, NO_REFERRAL_PATIENT)
    appt = call(server, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot_id)
    args = {"appointment_id": appt["appointment_id"], "patient_id": NO_REFERRAL_PATIENT}
    call(server, "cancel_appointment", **args)
    assert "already cancelled" in call_error(server, "cancel_appointment", **args)


def test_cancel_someone_elses_appointment_looks_like_not_found(server: MCPServer) -> None:
    slot_id = first_open_slot(server, PRIMARY_PROVIDER, NO_REFERRAL_PATIENT)
    appt = call(server, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot_id)
    other = SECOND_FREE_PATIENT
    wrong_owner = call_error(
        server, "cancel_appointment", appointment_id=appt["appointment_id"], patient_id=other
    )
    missing = call_error(server, "cancel_appointment", appointment_id="APT-99999", patient_id=other)
    assert f"not found for patient {other}" in wrong_owner
    assert wrong_owner.replace(appt["appointment_id"], "X") == missing.replace("APT-99999", "X")
    # And it is still booked.
    assert first_open_slot(server, PRIMARY_PROVIDER, NO_REFERRAL_PATIENT) != slot_id


# ---- get_referral_status ---------------------------------------------------------------


def test_referral_status_lists_patient_referrals(server: MCPServer) -> None:
    out = call(server, "get_referral_status", patient_id="PAT-0002")
    assert out["count"] == 2
    assert {r["referral_id"] for r in out["referrals"]} == {"REF-0002", "REF-0003"}
    dates = [r["issued_on"] for r in out["referrals"]]
    assert dates == sorted(dates, reverse=True)


def test_referral_status_filtered_by_specialty(server: MCPServer) -> None:
    out = call(server, "get_referral_status", patient_id="PAT-0002", specialty="cardiology")
    assert [(r["referral_id"], r["status"]) for r in out["referrals"]] == [("REF-0003", "pending")]


def test_referral_status_empty_for_patient_without_referrals(server: MCPServer) -> None:
    out = call(server, "get_referral_status", patient_id=NO_REFERRAL_PATIENT)
    assert out == {"patient_id": NO_REFERRAL_PATIENT, "referrals": [], "count": 0}


def test_ids_reject_non_ascii_digits(server: MCPServer) -> None:
    msg = call_error(server, "get_referral_status", patient_id="PAT-\u0660\u0660\u0660\u0661")
    assert "patient_id" in msg


def test_referral_status_unknown_patient(server: MCPServer) -> None:
    assert "not found" in call_error(server, "get_referral_status", patient_id="PAT-0999")


# ---- isolation ---------------------------------------------------------------------------


def test_each_default_server_has_its_own_store() -> None:
    a, b = build_server(), build_server()
    slot_id = first_open_slot(a, PRIMARY_PROVIDER)
    call(a, "book_appointment", patient_id=NO_REFERRAL_PATIENT, slot_id=slot_id)
    assert first_open_slot(b, PRIMARY_PROVIDER) == slot_id
