"""Typed tool results. TypedDict returns give each tool an output schema and structured_content."""

from typing import Literal, TypedDict

Specialty = Literal[
    "primary_care", "pediatrics", "cardiology", "dermatology", "orthopedics", "neurology"
]
Location = Literal["north", "south", "downtown"]
ReferralStatus = Literal["pending", "approved", "denied"]
AppointmentStatus = Literal["booked", "cancelled"]


class ProviderInfo(TypedDict):
    provider_id: str
    name: str
    specialty: Specialty
    location: Location
    requires_referral: bool


class SearchProvidersResult(TypedDict):
    providers: list[ProviderInfo]
    count: int


class SlotInfo(TypedDict):
    slot_id: str
    start: str
    end: str


class OpenSlotsResult(TypedDict):
    provider_id: str
    start_date: str
    end_date: str
    slots: list[SlotInfo]
    count: int
    truncated: bool


class AppointmentResult(TypedDict):
    appointment_id: str
    status: AppointmentStatus
    patient_id: str
    provider_id: str
    provider_name: str
    specialty: Specialty
    slot_id: str
    start: str
    end: str
    referral_id: str | None


class ReferralInfo(TypedDict):
    referral_id: str
    specialty: Specialty
    status: ReferralStatus
    issued_on: str


class ReferralStatusResult(TypedDict):
    patient_id: str
    referrals: list[ReferralInfo]
    count: int
