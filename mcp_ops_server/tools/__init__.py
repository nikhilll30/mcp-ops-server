"""Tool modules. Each exposes ``register(server, store)`` and wraps the store thinly."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_ops_server.store import StoreError

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

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)


@contextmanager
def store_errors() -> Iterator[None]:
    """Turn anticipated store failures into ToolErrors the agent can read and act on."""
    try:
        yield
    except StoreError as exc:
        raise ToolError(str(exc)) from exc
