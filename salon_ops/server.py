"""The salon's MCP server: its tools on the kit's server wiring (``mcp_ops_kit.server``)."""

from mcp.server.auth.provider import TokenVerifier
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette

from mcp_ops_kit import server as kit
from mcp_ops_kit.auth import Guard
from salon_ops import __version__, seed, tools
from salon_ops.auth import AUTH
from salon_ops.config import Settings
from salon_ops.store import SalonStore


def build_server(
    store: SalonStore | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    issuer_url: str = "http://127.0.0.1:8100",
    rate_limit_per_minute: int = 120,
) -> MCPServer:
    """The salon server; without ``store``, a fresh in-memory store seeded from ``seed.py``."""
    salon = store or SalonStore()

    def register(server: MCPServer, guard: Guard) -> None:
        tools.register(server, salon, guard)

    return kit.build_server(
        "salon-ops",
        __version__,
        AUTH,
        register,
        token_verifier=token_verifier,
        issuer_url=issuer_url,
        rate_limit_per_minute=rate_limit_per_minute,
    )


def make_store(settings: Settings) -> SalonStore:
    now = kit.make_clock(settings.now, seed.SALON_TZ)
    return SalonStore(settings.db_path, timeout_s=settings.call_timeout_s, now=now)


def build_http_app(settings: Settings, store: SalonStore | None = None) -> Starlette:
    """The app ``python -m salon_ops`` serves; the eval harness passes its own ``store``."""
    if not settings.tokens_file:
        raise ValueError("settings.tokens_file is required: the server never runs without auth")
    server = build_server(
        store or make_store(settings),
        token_verifier=AUTH.verifier.from_file(settings.tokens_file),
        issuer_url=f"http://{settings.host}:{settings.port}",
        rate_limit_per_minute=settings.rate_limit_per_minute,
    )
    return kit.build_http_app(server, settings, __version__)
