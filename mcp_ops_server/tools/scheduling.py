"""Provider search, open slots, booking and cancellation."""

from datetime import date
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_ops_server.auth import Guard
from mcp_ops_server.models import (
    AppointmentResult,
    Location,
    OpenSlotsResult,
    SearchProvidersResult,
    Specialty,
)
from mcp_ops_server.store import MAX_SLOT_RANGE_DAYS, Store
from mcp_ops_server.tools import (
    READ_ONLY,
    AppointmentId,
    PatientId,
    ProviderId,
    ReferralId,
    SlotId,
    store_errors,
    tool,
)


def register(server: MCPServer, store: Store, guard: Guard) -> None:
    @tool(server, annotations=READ_ONLY)
    def search_providers(
        specialty: Specialty | None = None,
        location: Location | None = None,
        name: Annotated[
            str | None,
            Field(min_length=1, max_length=50, description="Case-insensitive name substring."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> SearchProvidersResult:
        """Find providers by specialty, clinic location, and/or name.

        Each result says whether the specialty requires an approved referral before booking.
        """
        guard.check("read:schedule")
        with store_errors():
            return store.search_providers(specialty, location, name, limit)

    @tool(server, annotations=READ_ONLY)
    def list_open_slots(
        provider_id: ProviderId,
        start_date: Annotated[date, Field(description="First day to search (YYYY-MM-DD).")],
        end_date: Annotated[
            date | None,
            Field(
                description=f"Last day to search, inclusive. Defaults to start_date + 6 days. "
                f"At most {MAX_SLOT_RANGE_DAYS} days counting both ends, so end_date is at most "
                f"start_date + {MAX_SLOT_RANGE_DAYS - 1} days."
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> OpenSlotsResult:
        """List a provider's unbooked 30-minute slots, earliest first.

        Times are clinic-local ISO 8601 without offset. ``truncated`` is true when more slots
        exist than ``limit``; narrow the dates or raise the limit to see them.
        """
        guard.check("read:schedule")
        with store_errors():
            return store.list_open_slots(provider_id, start_date, end_date, limit)

    @tool(server, annotations=ToolAnnotations(destructiveHint=False, openWorldHint=False))
    def book_appointment(
        patient_id: PatientId,
        slot_id: SlotId,
        referral_id: Annotated[
            ReferralId | None,
            Field(description="Required for specialties that need a referral; must be approved."),
        ] = None,
    ) -> AppointmentResult:
        """Book an open slot for a patient.

        Fails if the slot is taken, the patient already has an overlapping appointment, or the
        specialty requires a referral and ``referral_id`` is missing, not approved, belongs to
        another patient, or is for a different specialty.
        """
        guard.check("write:schedule", patient_id)
        with store_errors():
            return store.book_appointment(patient_id, slot_id, referral_id)

    @tool(server, annotations=ToolAnnotations(destructiveHint=True, openWorldHint=False))
    def cancel_appointment(
        appointment_id: AppointmentId,
        patient_id: Annotated[
            PatientId, Field(description="Must match the appointment's patient.")
        ],
    ) -> AppointmentResult:
        """Cancel a booked appointment. The slot becomes open again."""
        guard.check("write:schedule", patient_id)
        with store_errors():
            return store.cancel_appointment(appointment_id, patient_id)
