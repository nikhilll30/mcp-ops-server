"""MCP server construction for the salon, wired the same way as the clinic's."""

from typing import Any, TypedDict

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from salon_ops import __version__, seed, tools
from salon_ops.auth import Guard, StaticTokenVerifier
from salon_ops.config import Settings
from salon_ops.limits import RateLimiter
from salon_ops.store import SalonStore


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
    store: SalonStore | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    issuer_url: str = "http://127.0.0.1:8100",
    rate_limit_per_minute: int = 120,
) -> MCPServer:
    store = store or SalonStore()
    auth = None
    if token_verifier is not None:
        auth = AuthSettings(issuer_url=issuer_url, resource_server_url=None)
    guard = Guard(RateLimiter(rate_limit_per_minute))
    server = OpsServer("salon-ops", auth=auth, token_verifier=token_verifier, guard=guard)

    @tools.tool(server)
    def health() -> HealthResult:
        """Liveness check. Returns server status and version. Counts toward the rate limit."""
        return {"status": "ok", "version": __version__}

    tools.register(server, store, guard)
    return server


def make_store(settings: Settings) -> SalonStore:
    fixed = settings.now
    now = (lambda: fixed) if fixed is not None else system_salon_now
    return SalonStore(settings.db_path, timeout_s=settings.call_timeout_s, now=now)


def system_salon_now():  # noqa: ANN201
    from datetime import datetime

    return datetime.now(seed.SALON_TZ).replace(tzinfo=None)


def build_http_app(settings: Settings, store: SalonStore | None = None) -> Starlette:
    if not settings.tokens_file:
        raise ValueError("settings.tokens_file is required: the server never runs without auth")
    verifier = StaticTokenVerifier.from_file(settings.tokens_file)
    server = build_server(
        store or make_store(settings),
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
    app.router.routes.append(Route("/healthz", _healthz, methods=["GET"]))
    return app


async def _healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__})
