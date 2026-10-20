"""End-to-end smoke test against a running salon server.

    MCP_URL=http://127.0.0.1:8100 MCP_TOKEN=<staff token> python -m salon_ops.smoke

The kit (``mcp_ops_kit.smoke``) checks the health probe and the 401 without a token. The
salon's round trip: health, a stylist search, and booking then cancelling a free opening a
week out (so the server is left as it was, and the cancel is outside the 24-hour window).
Each write is confirmed by reading the openings back. Exit code 1 on any failure.
"""

import sys
from typing import Any

from mcp_ops_kit import smoke
from mcp_ops_kit.smoke import call, check

CUSTOMER = "CUS-0005"  # synthetic customer with no seeded visits
STYLIST = "STY-04"  # Jasper: offers cut
DAY = "2026-11-10"


async def exercise(session: Any) -> None:  # noqa: ANN401 - an MCP ClientSession or a fake
    """The tool round trip on an initialized MCP session. Writes are confirmed by reading
    the openings back, not by trusting the write's own response."""

    async def free_opening_ids() -> set[str]:
        day = await call(
            session, "list_openings", stylist_id=STYLIST, start_date=DAY, end_date=DAY, limit=100
        )
        return {o["opening_id"] for o in day["openings"]}

    listed = await session.list_tools()
    check("tools listed", len(listed.tools) == 6, [t.name for t in listed.tools])
    check("health status", (await call(session, "health"))["status"] == "ok")
    found = await call(session, "search_stylists", service="cut")
    check("stylists found", len(found["stylists"]) > 0, len(found["stylists"]))

    before = await free_opening_ids()
    check("free openings found", len(before) > 0, len(before))
    opening_id = sorted(before)[0]
    booked = await call(
        session, "book_visit", customer_id=CUSTOMER, opening_id=opening_id, service="cut"
    )
    check(
        "booked the requested opening for the requested customer",
        booked["status"] == "booked"
        and booked["opening_id"] == opening_id
        and booked["customer_id"] == CUSTOMER,
        booked["visit_id"],
    )
    check("opening no longer free after booking", opening_id not in await free_opening_ids())
    cancelled = await call(
        session, "cancel_visit", visit_id=booked["visit_id"], customer_id=CUSTOMER
    )
    check(
        "cancelled the same visit",
        cancelled["status"] == "cancelled" and cancelled["visit_id"] == booked["visit_id"],
    )
    check("opening free again after cancelling", opening_id in await free_opening_ids())


def main() -> None:
    smoke.main(exercise, "http://127.0.0.1:8100")


if __name__ == "__main__":
    sys.exit(main())
