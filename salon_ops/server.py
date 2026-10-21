"""The salon's MCP server: its tools on the kit's server wiring (``mcp_ops_kit.server``)."""

from mcp.server.auth.provider import TokenVerifier
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette

from mcp_ops_kit import server as kit
from mcp_ops_kit.audit import AuditLog
from mcp_ops_kit.auth import Guard
from mcp_ops_kit.sqlite import require_key_for_file
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
    audit: AuditLog | None = None,
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
        audit=audit,
    )


def make_store(settings: Settings) -> SalonStore:
    """The store ``settings`` describe; a database file is always encrypted."""
    require_key_for_file(settings.db_path, settings.data_key, "SALON_OPS_DATA_KEY")
    now = kit.make_clock(settings.now, seed.SALON_TZ)
    return SalonStore(
        settings.db_path, timeout_s=settings.call_timeout_s, now=now, data_key=settings.data_key
    )


def build_http_app(settings: Settings, store: SalonStore | None = None) -> Starlette:
    """The app ``python -m salon_ops`` serves; the eval harness passes its own ``store``."""
    verifier = kit.build_verifier(settings, AUTH)
    audit = kit.open_audit(settings)
    try:
        server = build_server(
            store or make_store(settings),
            token_verifier=verifier,
            issuer_url=f"http://{settings.host}:{settings.port}",
            rate_limit_per_minute=settings.rate_limit_per_minute,
            audit=audit,
        )
    except BaseException:
        audit.close()
        raise
    return kit.build_http_app(server, settings, __version__)
