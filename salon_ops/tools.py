"""The salon's MCP tools: thin wrappers that check scope, then call the store."""

from datetime import date
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_ops_kit.auth import Guard
from mcp_ops_kit.server import READ_ONLY, store_errors, tool
from salon_ops.models import (
    CustomerVisitsResult,
    OpeningsResult,
    SearchStylistsResult,
    Service,
    Studio,
    VisitResult,
)
from salon_ops.store import MAX_RANGE_DAYS, SalonStore

CustomerId = Annotated[
    str, Field(pattern=r"^CUS-[0-9]{4}$", description="Customer ID, e.g. CUS-0001.")
]
StylistId = Annotated[
    str, Field(pattern=r"^STY-[0-9]{2}$", description="Stylist ID from search_stylists.")
]
OpeningId = Annotated[
    str, Field(pattern=r"^OPN-[0-9]{5}$", description="Opening ID from list_openings.")
]
VisitId = Annotated[
    str,
    Field(
        pattern=r"^VIS-[0-9]{5,18}$",
        description="Visit ID from book_visit or get_customer_visits.",
    ),
]


def register(server: MCPServer, store: SalonStore, guard: Guard) -> None:
    @tool(server, annotations=READ_ONLY)
    def search_stylists(
        service: Service | None = None,
        studio: Studio | None = None,
        name: Annotated[
            str | None,
            Field(min_length=1, max_length=50, description="Case-insensitive name substring."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> SearchStylistsResult:
        """Find stylists by service offered, studio, and/or name.

        Each result lists the services that stylist offers; a visit can only be booked for one
        of them.
        """
        guard.check("read:catalog")
        with store_errors():
            return store.search_stylists(service, studio, name, limit)

    @tool(server, annotations=READ_ONLY)
    def list_openings(
        stylist_id: StylistId,
        start_date: Annotated[date, Field(description="First day to search (YYYY-MM-DD).")],
        end_date: Annotated[
            date | None,
            Field(
                description=f"Last day to search, inclusive. Defaults to start_date + 6 days. "
                f"At most {MAX_RANGE_DAYS} days counting both ends, so end_date is at most "
                f"start_date + {MAX_RANGE_DAYS - 1} days."
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> OpeningsResult:
        """List a stylist's free 60-minute openings, earliest first.

        Times are salon-local ISO 8601 without offset. The salon opens Tuesday to Saturday.
        ``truncated`` is true when more openings exist than ``limit``.
        """
        guard.check("read:catalog")
        with store_errors():
            return store.list_openings(stylist_id, start_date, end_date, limit)

    @tool(server, annotations=READ_ONLY)
    def get_customer_visits(customer_id: CustomerId) -> CustomerVisitsResult:
        """List a customer's upcoming booked visits, earliest first."""
        guard.check("read:customers", customer_id)
        with store_errors():
            return store.get_customer_visits(customer_id)

    @tool(server, annotations=ToolAnnotations(destructiveHint=False, openWorldHint=False))
    def book_visit(customer_id: CustomerId, opening_id: OpeningId, service: Service) -> VisitResult:
        """Book a free opening for a customer and one service.

        Fails if the opening is taken or in the past, the stylist does not offer the service,
        the customer already has a visit at an overlapping time, or the customer already holds
        the maximum number of upcoming visits.
        """
        guard.check("write:bookings", customer_id)
        with store_errors():
            return store.book_visit(customer_id, opening_id, service)

    @tool(server, annotations=ToolAnnotations(destructiveHint=True, openWorldHint=False))
    def cancel_visit(
        visit_id: VisitId,
        customer_id: Annotated[CustomerId, Field(description="Must match the visit's customer.")],
    ) -> VisitResult:
        """Cancel a booked visit. The opening becomes free again.

        Visits starting within 24 hours cannot be cancelled online.
        """
        guard.check("write:bookings", customer_id)
        with store_errors():
            return store.cancel_visit(visit_id, customer_id)
