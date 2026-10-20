"""End-to-end smoke test against a running clinic server, e.g. the Docker image in CI.

    MCP_URL=http://127.0.0.1:8000 MCP_TOKEN=<staff token> python scripts/smoke_test.py

The kit (``mcp_ops_kit.smoke``) checks the unauthenticated health probe and that /mcp
refuses a request without a token. The clinic's round trip with the token: health, a
provider search, and booking then cancelling an open primary-care slot (so the server is
left as it was). Each write is confirmed by reading the schedule back: the slot must leave
the open list and return. Exit code 1 on any failure. The token needs all three scopes.
"""

import sys
from typing import Any

from mcp_ops_kit import smoke
from mcp_ops_kit.smoke import call, check

PATIENT = "PAT-0008"  # synthetic patient with no seeded bookings
PROVIDER = "PRV-001"  # primary care: no referral needed
WEEK_ONE = "2026-11-02"


async def exercise(session: Any) -> None:  # noqa: ANN401 - an MCP ClientSession or a fake
    """The tool round trip on an initialized MCP session. Writes are confirmed by reading
    the schedule back, not by trusting the write's own response."""

    async def open_slot_ids() -> set[str]:
        day = await call(
            session,
            "list_open_slots",
            provider_id=PROVIDER,
            start_date=WEEK_ONE,
            end_date=WEEK_ONE,
            limit=100,
        )
        return {s["slot_id"] for s in day["slots"]}

    listed = await session.list_tools()
    check("tools listed", len(listed.tools) == 6, [t.name for t in listed.tools])
    check("health status", (await call(session, "health"))["status"] == "ok")
    found = await call(session, "search_providers", specialty="cardiology")
    check("cardiologists found", len(found["providers"]) > 0, len(found["providers"]))

    before = await open_slot_ids()
    check("open slots found", len(before) > 0, len(before))
    slot_id = sorted(before)[0]
    booked = await call(session, "book_appointment", patient_id=PATIENT, slot_id=slot_id)
    check(
        "booked the requested slot for the requested patient",
        booked["status"] == "booked"
        and booked["slot_id"] == slot_id
        and booked["patient_id"] == PATIENT,
        booked["appointment_id"],
    )
    check("slot no longer open after booking", slot_id not in await open_slot_ids(), slot_id)
    cancelled = await call(
        session, "cancel_appointment", appointment_id=booked["appointment_id"], patient_id=PATIENT
    )
    check(
        "cancelled the same appointment",
        cancelled["status"] == "cancelled"
        and cancelled["appointment_id"] == booked["appointment_id"],
    )
    check("slot open again after cancelling", slot_id in await open_slot_ids(), slot_id)


def main() -> None:
    smoke.main(exercise, "http://127.0.0.1:8000")


if __name__ == "__main__":
    sys.exit(main())
