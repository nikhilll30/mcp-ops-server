"""MCP server construction and tool registration."""

from typing import TypedDict

from mcp.server.mcpserver import MCPServer

from mcp_ops_server import __version__
from mcp_ops_server.store import Store
from mcp_ops_server.tools import referrals, scheduling


class HealthResult(TypedDict):
    status: str
    version: str


def build_server(store: Store | None = None) -> MCPServer:
    """Build and configure the MCP ops server.

    Each call without ``store`` gets a fresh in-memory store seeded from ``seed.py``.
    """
    store = store or Store()
    server = MCPServer("mcp-ops-server")

    @server.tool()
    def health() -> HealthResult:
        """Liveness check. Returns server status and version."""
        return {"status": "ok", "version": __version__}

    scheduling.register(server, store)
    referrals.register(server, store)
    return server
