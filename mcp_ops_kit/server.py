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
from dataclasses import dataclass, field, fields
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, TypedDict

import pydantic_core
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_ops_kit.audit import AuditLog
from mcp_ops_kit.auth import AuthConfig, Guard
from mcp_ops_kit.crypto import DataKey
from mcp_ops_kit.errors import ConfigError, StoreError
from mcp_ops_kit.limits import RateLimiter
from mcp_ops_kit.oauth import EitherVerifier, JwksSource, JwtVerifier, RevocationList

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
    """MCPServer that audits every tool call (if given an ``AuditLog``) and rate-limits it,
    both before the SDK validates arguments, so no call escapes either.

    A call's audit outcome: ``rate_limited``; ``denied`` (the guard refused it); ``invalid``
    (it failed before reaching the guard: bad arguments, unknown tool); ``error`` (it failed
    after the guard let it through, e.g. a slot already taken); or ``ok``.

    Data minimisation: the SDK validates a tool's result against its output schema and
    drops undeclared keys from ``structured_content``, but renders the *text* content (what
    a model reads) from the raw return value. A store returning an extra field would leak
    it there. So the text is rebuilt from the validated ``structured_content``: an agent
    sees exactly the declared fields, whatever a backend returns.
    """

    def __init__(
        self, *args: Any, guard: Guard, audit: AuditLog | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._guard = guard
        self.audit = audit

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> Any:  # noqa: ANN401 - mirrors the SDK signature
        if self.audit is None:
            self._guard.rate_limit()
            return _declared_only(await super().call_tool(name, arguments, context))
        call, reset = self.audit.begin(name)
        try:
            self._guard.rate_limit()
            result = await super().call_tool(name, arguments, context)
        except BaseException as exc:  # cancellation too: no record may lack an outcome
            call.outcome = call.outcome or ("error" if call.checked else "invalid")
            call.error = type(_store_cause(exc) or exc).__name__
            raise
        else:
            call.outcome = call.outcome or "ok"
            return _declared_only(result)
        finally:
            self.audit.end(call, reset)


def _declared_only(result: Any) -> Any:  # noqa: ANN401 - whatever the SDK returned
    if not isinstance(result, CallToolResult) or result.is_error:
        return result
    if result.structured_content is None:
        return result
    text = pydantic_core.to_json(result.structured_content, fallback=str, indent=2).decode()
    return result.model_copy(update={"content": [TextContent(type="text", text=text)]})


def _store_cause(exc: BaseException) -> StoreError | None:
    """The store error behind a tool failure (the SDK wraps the tool's ToolError, which
    wraps it), so the audit log can say ``ConflictError`` rather than ``ToolError``."""
    seen: BaseException | None = exc
    for _ in range(5):
        if seen is None or isinstance(seen, StoreError):
            return seen
        seen = seen.__cause__
    return None


def build_server(
    name: str,
    version: str,
    auth: AuthConfig,
    register: Callable[[MCPServer, Guard], None],
    *,
    token_verifier: TokenVerifier | None = None,
    issuer_url: str = "http://127.0.0.1:8000",
    rate_limit_per_minute: int = 120,
    audit: AuditLog | None = None,
) -> MCPServer:
    """An ``OpsServer`` with a ``health`` tool, then the domain's tools from ``register``.

    With ``token_verifier``, the HTTP transport rejects requests without a valid bearer
    token (401). Scope and subject checks run inside every domain tool regardless (through
    the ``Guard`` passed to ``register``), so an in-process call with no token is refused too.
    With ``audit``, every tool call is recorded (``build_http_app`` always passes one).
    """
    settings = None
    if token_verifier is not None:
        # Resource-server-only setup: we verify tokens, we do not issue them over OAuth.
        settings = AuthSettings(issuer_url=issuer_url, resource_server_url=None)
    guard = Guard(auth, RateLimiter(rate_limit_per_minute))
    server = OpsServer(name, auth=settings, token_verifier=token_verifier, guard=guard, audit=audit)

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
    # The audience this server accepts; None means the domain's (``AuthConfig.audience``).
    audience: str | None = None
    # OAuth 2.1 JWT access tokens (optional, alongside or instead of the tokens file).
    jwt_issuer: str | None = None
    jwt_jwks: str | None = None  # a JWKS file, or its https:// URL
    jwt_revoked_file: str | None = None
    # The audit log file (required to serve) and the data key that encrypts it and any
    # database file. The key never appears in a repr.
    audit_log: str | None = None
    data_key: DataKey | None = field(default=None, repr=False)

    # Settings where unset or empty both mean "not configured".
    _OPTIONAL = (
        "tokens_file",
        "audience",
        "jwt_issuer",
        "jwt_jwks",
        "jwt_revoked_file",
        "audit_log",
    )

    @classmethod
    def common_from_env(cls, prefix: str) -> dict[str, Any]:
        """The shared fields from ``<prefix>HOST``, ``<prefix>PORT`` and so on, with this
        class's defaults. ``<prefix>NOW`` is an ISO time, or ``system`` for the real clock.
        ``<prefix>DATA_KEY`` is a base64url key (``python -m mcp_ops_kit.crypto new-key``)."""
        env = os.environ
        default = cls()
        now_raw = env.get(f"{prefix}NOW", "")
        if now_raw == "system":
            now = None
        elif now_raw:
            now = datetime.fromisoformat(now_raw)
        else:
            now = default.now
        key_raw = env.get(f"{prefix}DATA_KEY")
        data_key = DataKey.from_text(key_raw, f"{prefix}DATA_KEY") if key_raw else None
        casts: dict[str, Callable[[str], Any]] = {"port": int, "rate_limit_per_minute": int}
        casts |= {"max_body_bytes": int, "max_sessions": int, "call_timeout_s": float}
        casts |= {"session_idle_timeout_s": float}
        names = {"rate_limit_per_minute": "RATE_LIMIT_PER_MIN"}
        out: dict[str, Any] = {"now": now, "data_key": data_key}
        for f in fields(ServerSettings):
            if f.name in ("now", "data_key"):
                continue
            var = prefix + names.get(f.name, f.name.upper())
            raw = env.get(var)
            if f.name in cls._OPTIONAL:
                out[f.name] = raw or None  # unset or empty: not configured
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


def audience(settings: ServerSettings, auth: AuthConfig) -> str:
    return settings.audience or auth.audience


def build_verifier(settings: ServerSettings, auth: AuthConfig) -> TokenVerifier:
    """The token verifier ``settings`` describe: the tokens file, OAuth JWTs, or both. Fails
    closed (``ConfigError``) when neither is configured or either is half-configured."""
    aud = audience(settings, auth)
    static = None
    if settings.tokens_file:
        static = auth.verifier.from_file(settings.tokens_file, audience=aud)
    jwt_verifier = None
    if settings.jwt_issuer or settings.jwt_jwks:
        if not (settings.jwt_issuer and settings.jwt_jwks):
            raise ConfigError("JWT access tokens need both an issuer and a JWKS (file or URL)")
        revocations = (
            RevocationList(settings.jwt_revoked_file) if settings.jwt_revoked_file else None
        )
        jwt_verifier = JwtVerifier(
            auth,
            issuer=settings.jwt_issuer,
            audience=aud,
            jwks=JwksSource(settings.jwt_jwks, timeout_s=settings.call_timeout_s),
            revocations=revocations,
        )
    if static is None and jwt_verifier is None:
        raise ConfigError(
            "settings.tokens_file or a JWT issuer is required: the server never runs without auth"
        )
    return EitherVerifier(static, jwt_verifier)


def open_audit(settings: ServerSettings) -> AuditLog:
    """The audit log ``settings`` name. Required: the server never runs without one."""
    if not settings.audit_log:
        raise ConfigError(
            "settings.audit_log is required: the server never runs without an audit log"
        )
    if settings.data_key is None:
        raise ConfigError("settings.data_key is required: the audit log is always encrypted")
    return AuditLog(settings.audit_log, settings.data_key)


def build_http_app(
    server: MCPServer,
    settings: ServerSettings,
    version: str,
) -> Starlette:
    """The Streamable HTTP app at ``/mcp`` (bearer token required), plus an unauthenticated
    ``/healthz`` liveness probe for container orchestrators that returns no data. The app
    keeps the server's audit log as ``app.state.audit`` (``None`` if it has none), for
    whoever owns the app's lifetime to close."""
    app = server.streamable_http_app(
        host=settings.host,
        max_request_body_size=settings.max_body_bytes,
        max_sessions=settings.max_sessions,
        session_idle_timeout=settings.session_idle_timeout_s,
    )

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": version})

    app.router.routes.append(Route("/healthz", healthz, methods=["GET"]))
    app.state.audit = getattr(server, "audit", None)
    return app


def serve(
    settings: ServerSettings,
    build_app: Callable[[ServerSettings], Starlette],
    *,
    auth: AuthConfig,
    env_prefix: str,
    startup_errors: tuple[type[Exception], ...] = (),
) -> None:
    """Fail-closed entry point: never serve without a way to verify tokens (a tokens file or
    a JWT issuer), an audit log and a data key, and turn an expected start-up failure (a
    ``ConfigError``, or ``startup_errors`` such as an unreachable backend) into one line."""
    import uvicorn

    if not settings.tokens_file and not settings.jwt_issuer:
        sys.exit(
            f"{env_prefix}TOKENS_FILE is not set (nor {env_prefix}JWT_ISSUER). Issue a token "
            f"first:\n  {auth.issue_hint()}\n"
            f"then run with {env_prefix}TOKENS_FILE=tokens.json"
        )
    if not settings.audit_log or settings.data_key is None:
        sys.exit(
            f"{env_prefix}AUDIT_LOG and {env_prefix}DATA_KEY are both required: every tool "
            f"call is written to an encrypted audit log. Make a key with\n"
            f"  python -m mcp_ops_kit.crypto new-key\n"
            f"then run with {env_prefix}DATA_KEY=<key> {env_prefix}AUDIT_LOG=audit.log"
        )
    if settings.tokens_file and not Path(settings.tokens_file).is_file():
        # Same rule, with a message instead of a traceback (e.g. a container started
        # before a tokens file was mounted).
        sys.exit(
            f"Tokens file {settings.tokens_file} not found. Issue a token into it first "
            f"({auth.cli_prog} issue --file ...) or point {env_prefix}TOKENS_FILE at it."
        )
    try:
        app = build_app(settings)
    except (ConfigError, *startup_errors) as exc:
        sys.exit(str(exc))
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
