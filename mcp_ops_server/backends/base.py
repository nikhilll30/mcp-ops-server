"""The ``Backend`` protocol every data store implements, and the errors tools may show.

Tools depend on this module only. Each backend (SQLite, FHIR R4) enforces the same integrity
rules and raises the same errors with the same messages; ``tests/test_tools.py`` runs every
tool test against each backend to keep them identical.
"""

from datetime import date, timedelta
from typing import Protocol, TypedDict

from mcp_ops_server.models import (
    AppointmentResult,
    AppointmentStatus,
    OpenSlotsResult,
    ReferralStatusResult,
    SearchProvidersResult,
)

MAX_SLOT_RANGE_DAYS = 31
# Far wider than the 4-week schedule; keeps date arithmetic away from date.max.
EARLIEST_DATE = date(2000, 1, 1)
LATEST_DATE = date(2100, 12, 31)


class StoreError(Exception):
    """An anticipated failure whose message is safe and useful to show an agent."""


class NotFoundError(StoreError):
    pass


class ConflictError(StoreError):
    pass


class ReferralError(StoreError):
    pass


class BusyError(StoreError):
    """The data store could not serve the call in time (lock held, contention, or offline)."""


class CallTimeoutError(StoreError):
    """A call ran past the per-call deadline and was cancelled. Nothing was written."""


class OutcomeUnknownError(StoreError):
    """A write was sent but not confirmed in time, and a read-back could not settle whether
    it landed. Never raised for a write known to have failed or succeeded."""


class AppointmentRow(TypedDict):
    """One appointment as the eval harness snapshots it (keyed by its number, APT-<n>)."""

    status: AppointmentStatus
    patient_id: str
    referral_id: str | None
    slot_id: str
    start: str
    provider_id: str
    specialty: str
    location: str


class Backend(Protocol):
    """Everything the tools and the eval harness need from a data store.

    Times are clinic-local ISO 8601 without offset, whatever the store keeps internally.
    """

    def search_providers(
        self,
        specialty: str | None = None,
        location: str | None = None,
        name: str | None = None,
        limit: int = 20,
    ) -> SearchProvidersResult: ...

    def list_open_slots(
        self,
        provider_id: str,
        start_date: date,
        end_date: date | None = None,
        limit: int = 20,
    ) -> OpenSlotsResult: ...

    def get_referral_status(
        self, patient_id: str, specialty: str | None = None
    ) -> ReferralStatusResult: ...

    def book_appointment(
        self, patient_id: str, slot_id: str, referral_id: str | None = None
    ) -> AppointmentResult: ...

    def cancel_appointment(self, appointment_id: str, patient_id: str) -> AppointmentResult: ...

    def snapshot_appointments(self) -> dict[int, AppointmentRow]:
        """Every appointment, booked or cancelled, keyed by its number."""
        ...

    def close(self) -> None: ...


def appointment_id(n: int) -> str:
    return f"APT-{n:05d}"


def parse_appointment_id(value: str) -> int | None:
    prefix, _, digits = value.partition("-")
    ok = prefix == "APT" and digits.isascii() and digits.isdigit() and len(digits) <= 18
    return int(digits) if ok else None


def slot_range(start_date: date, end_date: date | None) -> date:
    """Validate a list_open_slots date range and return its (defaulted) end date."""
    for d in (start_date, end_date):
        if d is not None and not EARLIEST_DATE <= d <= LATEST_DATE:
            raise StoreError(
                f"Dates must be between {EARLIEST_DATE.isoformat()} and {LATEST_DATE.isoformat()}."
            )
    end_date = end_date or start_date + timedelta(days=6)
    if end_date < start_date:
        raise StoreError("end_date must be on or after start_date.")
    if (end_date - start_date).days >= MAX_SLOT_RANGE_DAYS:
        latest = start_date + timedelta(days=MAX_SLOT_RANGE_DAYS - 1)
        raise StoreError(
            f"Date range is limited to {MAX_SLOT_RANGE_DAYS} days counting both ends: "
            f"for start_date {start_date.isoformat()} the latest end_date is "
            f"{latest.isoformat()}."
        )
    return end_date


# Error messages shared by both backends, so an agent sees the same text either way.


def provider_not_found(provider_id: str) -> NotFoundError:
    return NotFoundError(
        f"Provider {provider_id} not found. Call search_providers to find provider IDs."
    )


def patient_not_found(patient_id: str) -> NotFoundError:
    return NotFoundError(f"Patient {patient_id} not found.")


def slot_not_found(slot_id: str) -> NotFoundError:
    return NotFoundError(f"Slot {slot_id} not found. Call list_open_slots to find open slots.")


def slot_in_past(slot_id: str, start: str) -> ConflictError:
    return ConflictError(
        f"Slot {slot_id} starts at {start}, which is in the past. "
        "Call list_open_slots for an upcoming slot."
    )


def slot_taken(slot_id: str, provider_id: str, start: str) -> ConflictError:
    # Name the slot's provider and time: nothing else lets a caller find "the next slot with
    # the same provider that day" (no tool looks a slot up by ID).
    return ConflictError(
        f"Slot {slot_id} ({provider_id}, {start}) is already booked. "
        "Call list_open_slots for another slot."
    )


def patient_clash(patient_id: str, other_appointment: int) -> ConflictError:
    return ConflictError(
        f"Patient {patient_id} already has appointment "
        f"{appointment_id(other_appointment)} at an overlapping time."
    )


def check_referral(
    patient_id: str,
    specialty: str,
    required: bool,
    referral_id: str | None,
    referral: tuple[str, str, str] | None,
) -> None:
    """Referral rules. ``referral`` is (patient_id, specialty, status) of ``referral_id``."""
    if referral_id is None:
        if required:
            raise ReferralError(
                f"{specialty} requires an approved referral. Call get_referral_status for "
                f"patient {patient_id} and pass an approved referral_id."
            )
        return
    if referral is None or referral[0] != patient_id:
        raise ReferralError(f"Referral {referral_id} not found for patient {patient_id}.")
    if referral[1] != specialty:
        raise ReferralError(f"Referral {referral_id} is for {referral[1]}, not {specialty}.")
    if referral[2] != "approved":
        raise ReferralError(f"Referral {referral_id} is {referral[2]}, not approved.")


def appointment_not_found(appointment: str, patient_id: str) -> NotFoundError:
    # Same message whether the ID is unknown or belongs to someone else.
    return NotFoundError(f"Appointment {appointment} not found for patient {patient_id}.")


def already_cancelled(appointment: str) -> ConflictError:
    return ConflictError(f"Appointment {appointment} is already cancelled.")


def already_started(appointment: str, start: str) -> ConflictError:
    return ConflictError(
        f"Appointment {appointment} started at {start}; it can no longer be cancelled."
    )
