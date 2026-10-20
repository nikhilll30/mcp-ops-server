"""The clinic's MCP server: its tools on the kit's server wiring (``mcp_ops_kit.server``)."""

from mcp.server.auth.provider import TokenVerifier
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette

from mcp_ops_kit import server as kit
from mcp_ops_kit.audit import AuditLog
from mcp_ops_kit.auth import Guard
from mcp_ops_server import __version__
from mcp_ops_server.auth import AUTH
from mcp_ops_server.backends import make_backend
from mcp_ops_server.backends.base import Backend
from mcp_ops_server.backends.sqlite import SqliteBackend
from mcp_ops_server.config import Settings
from mcp_ops_server.tools import referrals, scheduling


def build_server(
    store: Backend | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    issuer_url: str = "http://127.0.0.1:8000",
    rate_limit_per_minute: int = 120,
    audit: AuditLog | None = None,
) -> MCPServer:
    """Build and configure the MCP ops server.

    Each call without ``store`` gets a fresh in-memory store seeded from ``seed.py``.
    With ``token_verifier``, the HTTP transport rejects requests without a valid bearer
    token (401). Scope and patient checks run inside every tool regardless, so an
    in-process call with no authenticated token is refused too.
    """
    backend = store or SqliteBackend()

    def register(server: MCPServer, guard: Guard) -> None:
        scheduling.register(server, backend, guard)
        referrals.register(server, backend, guard)

    return kit.build_server(
        "mcp-ops-server",
        __version__,
        AUTH,
        register,
        token_verifier=token_verifier,
        issuer_url=issuer_url,
        rate_limit_per_minute=rate_limit_per_minute,
        audit=audit,
    )


def build_http_app(settings: Settings, backend: Backend | None = None) -> Starlette:
    """The Streamable HTTP app exactly as ``python -m mcp_ops_server`` serves it.

    Shared with the eval harness, so evals run the same auth, audit, limits and clock
    wiring. The harness passes its own ``backend`` (built with ``make_backend``) to
    snapshot it. Raises ``ConfigError`` without a token source, an audit log or a data key.
    """
    verifier = kit.build_verifier(settings, AUTH)
    audit = kit.open_audit(settings)
    try:
        server = build_server(
            backend or make_backend(settings),
            token_verifier=verifier,
            issuer_url=f"http://{settings.host}:{settings.port}",
            rate_limit_per_minute=settings.rate_limit_per_minute,
            audit=audit,
        )
    except BaseException:
        audit.close()
        raise
    return kit.build_http_app(server, settings, __version__)
