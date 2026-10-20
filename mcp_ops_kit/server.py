"""MCP server wiring shared by every domain: rate limit first, tools, auth, HTTP, start-up.

A domain calls ``build_server`` with a function that registers its tools, then
``build_http_app`` to serve it, and ``serve`` from its ``__main__``. Everything a domain
tool needs (``tool``, ``store_errors``, ``READ_ONLY``) is here too.
"""

import inspect
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, TypedDict

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_ops_kit.auth import AuthConfig, Guard
from mcp_ops_kit.errors import StoreError
from mcp_ops_kit.limits import RateLimiter

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)


class HealthResult(TypedDict):
    status: str
    version: str


@contextmanager
def store_errors() -> Iterator[None]:
    """Turn anticipated store failures into ToolErrors the agent can read and act on."""
    try:
        yield
    except StoreError as exc:
        raise ToolError(str(exc)) from exc


def tool(server: MCPServer, **kwargs: object) -> Callable[[Callable], Callable]:
    """Register a tool whose description is its docstring, dedented the same on every Python.

    Python 3.13 dedents docstrings at compile time and 3.12 does not, so a raw ``__doc__``
    gives agents different (indented) descriptions depending on the interpreter.
    """

    def register(fn: Callable) -> Callable:
        return server.tool(description=inspect.cleandoc(fn.__doc__ or ""), **kwargs)(fn)

    return register


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
    name: str,
    version: str,
    auth: AuthConfig,
    register: Callable[[MCPServer, Guard], None],
    *,
    token_verifier: TokenVerifier | None = None,
    issuer_url: str = "http://127.0.0.1:8000",
    rate_limit_per_minute: int = 120,
) -> MCPServer:
    """An ``OpsServer`` with a ``health`` tool, then the domain's tools from ``register``.

    With ``token_verifier``, the HTTP transport rejects requests without a valid bearer
    token (401). Scope and subject checks run inside every domain tool regardless (through
    the ``Guard`` passed to ``register``), so an in-process call with no token is refused too.
    """
    settings = None
    if token_verifier is not None:
        # Resource-server-only setup: we verify tokens, we do not issue them over OAuth.
        settings = AuthSettings(issuer_url=issuer_url, resource_server_url=None)
    guard = Guard(auth, RateLimiter(rate_limit_per_minute))
    server = OpsServer(name, auth=settings, token_verifier=token_verifier, guard=guard)

    @tool(server)
    def health() -> HealthResult:
        """Liveness check. Returns server status and version. Counts toward the rate limit."""
        return {"status": "ok", "version": version}

    register(server, guard)
    return server


@dataclass(frozen=True)
class ServerSettings:
    """Settings every domain's server has. A domain subclasses it with its own fields
    (database path, backend, clock default) and reads them in its own ``from_env``."""

    host: str = "127.0.0.1"
    port: int = 8000
    tokens_file: str | None = None
    rate_limit_per_minute: int = 120
    max_body_bytes: int = 64 * 1024
    call_timeout_s: float = 5.0
    max_sessions: int = 100
    session_idle_timeout_s: float = 600.0
    # The domain's simulated local time; None means the real clock (see ``make_clock``).
    now: datetime | None = None

    @classmethod
    def common_from_env(cls, prefix: str) -> dict[str, Any]:
        """The shared fields from ``<prefix>HOST``, ``<prefix>PORT`` and so on, with this
        class's defaults. ``<prefix>NOW`` is an ISO time, or ``system`` for the real clock."""
        env = os.environ
        default = cls()
        now_raw = env.get(f"{prefix}NOW", "")
        if now_raw == "system":
            now = None
        elif now_raw:
            now = datetime.fromisoformat(now_raw)
        else:
            now = default.now
        casts: dict[str, Callable[[str], Any]] = {"port": int, "rate_limit_per_minute": int}
        casts |= {"max_body_bytes": int, "max_sessions": int, "call_timeout_s": float}
        casts |= {"session_idle_timeout_s": float}
        names = {"rate_limit_per_minute": "RATE_LIMIT_PER_MIN"}
        out: dict[str, Any] = {"now": now}
        for f in fields(ServerSettings):
            if f.name == "now":
                continue
            var = prefix + names.get(f.name, f.name.upper())
            raw = env.get(var)
            if f.name == "tokens_file":
                out[f.name] = raw or None  # unset or empty: no tokens file, so serve refuses
            elif raw is None:
                out[f.name] = getattr(default, f.name)
            else:
                # Set but empty or malformed is a configuration mistake, never the default
                # (Codex review, PR #18: an empty MCP_OPS_PORT used to fail, and must still).
                try:
                    out[f.name] = casts.get(f.name, str)(raw)
                except ValueError as exc:
                    raise ValueError(f"{var}={raw!r} is not a valid {f.name}") from exc
        return out


def make_clock(now: datetime | None, tz: tzinfo) -> Callable[[], datetime]:
    """The domain clock: pinned to ``now``, or the real clock as wall time in ``tz`` (naive,
    like every time the tools use). Not ``datetime.now()``: that is the host's local time,
    which is UTC in a container (Codex review, PR #16)."""
    if now is not None:
        fixed = now
        return lambda: fixed
    return lambda: datetime.now(tz).replace(tzinfo=None)


def build_http_app(
    server: MCPServer,
    settings: ServerSettings,
    version: str,
) -> Starlette:
    """The Streamable HTTP app at ``/mcp`` (bearer token required), plus an unauthenticated
    ``/healthz`` liveness probe for container orchestrators that returns no data."""
    app = server.streamable_http_app(
        host=settings.host,
        max_request_body_size=settings.max_body_bytes,
        max_sessions=settings.max_sessions,
        session_idle_timeout=settings.session_idle_timeout_s,
    )

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": version})

    app.router.routes.append(Route("/healthz", healthz, methods=["GET"]))
    return app


def serve(
    settings: ServerSettings,
    build_app: Callable[[ServerSettings], Starlette],
    *,
    auth: AuthConfig,
    env_prefix: str,
    startup_errors: tuple[type[Exception], ...] = (),
) -> None:
    """Fail-closed entry point: never serve without a tokens file, and turn an expected
    start-up failure (``startup_errors``, e.g. an unreachable backend) into one line."""
    import uvicorn

    if not settings.tokens_file:
        sys.exit(
            f"{env_prefix}TOKENS_FILE is not set. Issue a token first:\n"
            f"  {auth.issue_hint()}\n"
            f"then run with {env_prefix}TOKENS_FILE=tokens.json"
        )
    if not Path(settings.tokens_file).is_file():
        # Same rule, with a message instead of a traceback (e.g. a container started
        # before a tokens file was mounted).
        sys.exit(
            f"Tokens file {settings.tokens_file} not found. Issue a token into it first "
            f"({auth.cli_prog} issue --file ...) or point {env_prefix}TOKENS_FILE at it."
        )
    try:
        app = build_app(settings)
    except startup_errors as exc:
        sys.exit(str(exc))
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
