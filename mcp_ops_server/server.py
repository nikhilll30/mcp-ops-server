"""MCP server construction and tool registration."""

from typing import Any, TypedDict

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_ops_server import __version__
from mcp_ops_server.auth import Guard, StaticTokenVerifier
from mcp_ops_server.backends import make_backend
from mcp_ops_server.backends.base import Backend
from mcp_ops_server.backends.sqlite import SqliteBackend
from mcp_ops_server.config import Settings
from mcp_ops_server.limits import RateLimiter
from mcp_ops_server.tools import referrals, scheduling, tool


class HealthResult(TypedDict):
    status: str
    version: str


class OpsServer(MCPServer):
    """MCPServer that rate-limits every tool call before the SDK validates arguments."""

    def __init__(self, *args: Any, guard: Guard, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._guard = guard

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> Any:  # noqa: ANN401 - mirrors the SDK signature
        self._guard.rate_limit()
        return await super().call_tool(name, arguments, context)


def build_server(
    store: Backend | None = None,
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
    store = store or SqliteBackend()
    auth = None
    if token_verifier is not None:
        # Resource-server-only setup: we verify tokens, we do not issue them over OAuth.
        auth = AuthSettings(issuer_url=issuer_url, resource_server_url=None)
    guard = Guard(RateLimiter(rate_limit_per_minute))
    server = OpsServer("mcp-ops-server", auth=auth, token_verifier=token_verifier, guard=guard)

    @tool(server)
    def health() -> HealthResult:
        """Liveness check. Returns server status and version. Counts toward the rate limit."""
        return {"status": "ok", "version": __version__}

    scheduling.register(server, store, guard)
    referrals.register(server, store, guard)
    return server


def build_http_app(settings: Settings, backend: Backend | None = None) -> Starlette:
    """The Streamable HTTP app exactly as ``python -m mcp_ops_server`` serves it.

    Shared with the eval harness, so evals run the same auth, limits and clock wiring. The
    harness passes its own ``backend`` (built with ``make_backend``) to snapshot it.
    """
    if not settings.tokens_file:
        raise ValueError("settings.tokens_file is required: the server never runs without auth")
    verifier = StaticTokenVerifier.from_file(settings.tokens_file)
    server = build_server(
        backend or make_backend(settings),
        token_verifier=verifier,
        issuer_url=f"http://{settings.host}:{settings.port}",
        rate_limit_per_minute=settings.rate_limit_per_minute,
    )
    app = server.streamable_http_app(
        host=settings.host,
        max_request_body_size=settings.max_body_bytes,
        max_sessions=settings.max_sessions,
        session_idle_timeout=settings.session_idle_timeout_s,
    )
    # Unauthenticated liveness probe for container orchestrators. It returns no data;
    # everything under /mcp still requires a bearer token.
    app.router.routes.append(Route("/healthz", _healthz, methods=["GET"]))
    return app


async def _healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__})
