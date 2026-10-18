"""Referral lookups."""

from mcp.server.mcpserver import MCPServer

from mcp_ops_server.models import ReferralStatusResult, Specialty
from mcp_ops_server.store import Store
from mcp_ops_server.tools import READ_ONLY, PatientId, store_errors


def register(server: MCPServer, store: Store) -> None:
    @server.tool(annotations=READ_ONLY)
    def get_referral_status(
        patient_id: PatientId, specialty: Specialty | None = None
    ) -> ReferralStatusResult:
        """List a patient's referrals (newest first), optionally for one specialty.

        Only a referral with status ``approved`` can be used to book a specialty that requires one.
        """
        with store_errors():
            return store.get_referral_status(patient_id, specialty)
