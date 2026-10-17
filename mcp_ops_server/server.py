"""MCP server construction and tool registration."""

from typing import TypedDict

from mcp.server.mcpserver import MCPServer

from mcp_ops_server import __version__


class HealthResult(TypedDict):
    status: str
    version: str


def build_server() -> MCPServer:
    """Build and configure the MCP ops server."""
    server = MCPServer("mcp-ops-server")

    @server.tool()
    def health() -> HealthResult:
        """Liveness check. Returns server status and version."""
        return {"status": "ok", "version": __version__}

    return server
