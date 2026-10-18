"""MCP server construction and tool registration."""

from typing import TypedDict

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

from mcp_ops_server import __version__
from mcp_ops_server.auth import Guard
from mcp_ops_server.limits import RateLimiter
from mcp_ops_server.store import Store
from mcp_ops_server.tools import referrals, scheduling


class HealthResult(TypedDict):
    status: str
    version: str


def build_server(
    store: Store | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    issuer_url: str = "http://127.0.0.1:8000",
    rate_limit_per_minute: int = 120,
) -> MCPServer:
    """Build and configure the MCP ops server.

    Each call without ``store`` gets a fresh in-memory store seeded from ``seed.py``.
    With ``token_verifier``, the HTTP transport rejects requests without a valid bearer
    token (401). Scope and patient checks run inside every tool regardless, so an
    in-process call with no authenticated token is refused too.
    """
    store = store or Store()
    auth = None
    if token_verifier is not None:
        # Resource-server-only setup: we verify tokens, we do not issue them over OAuth.
        auth = AuthSettings(issuer_url=issuer_url, resource_server_url=None)
    server = MCPServer("mcp-ops-server", auth=auth, token_verifier=token_verifier)
    guard = Guard(RateLimiter(rate_limit_per_minute))

    @server.tool()
    def health() -> HealthResult:
        """Liveness check. Returns server status and version."""
        return {"status": "ok", "version": __version__}

    scheduling.register(server, store, guard)
    referrals.register(server, store, guard)
    return server
