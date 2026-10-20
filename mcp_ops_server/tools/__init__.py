"""Tool modules. Each exposes ``register(server, store, guard)`` and wraps the store thinly.

The registration helpers (``tool``, ``store_errors``, ``READ_ONLY``) come from the kit; the
clinic's ID types live here.
"""

from typing import Annotated

from pydantic import Field

from mcp_ops_kit.server import READ_ONLY, store_errors, tool

__all__ = ["READ_ONLY", "store_errors", "tool"]

PatientId = Annotated[
    str, Field(pattern=r"^PAT-[0-9]{4}$", description="Patient ID, e.g. PAT-0001.")
]
ProviderId = Annotated[
    str, Field(pattern=r"^PRV-[0-9]{3}$", description="Provider ID from search_providers.")
]
SlotId = Annotated[
    str, Field(pattern=r"^SLT-[0-9]{5}$", description="Slot ID from list_open_slots.")
]
ReferralId = Annotated[
    str, Field(pattern=r"^REF-[0-9]{4}$", description="Referral ID from get_referral_status.")
]
AppointmentId = Annotated[
    str, Field(pattern=r"^APT-[0-9]{5,18}$", description="Appointment ID from book_appointment.")
]
