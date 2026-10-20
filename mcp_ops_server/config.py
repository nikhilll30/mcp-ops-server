"""Runtime settings. The only module that reads environment variables (see .env.example)."""

import os
from dataclasses import dataclass
from datetime import datetime

from mcp_ops_server import seed

BACKENDS = ("sqlite", "fhir")


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    backend: str = "sqlite"  # "sqlite" or "fhir"
    db_path: str = ":memory:"
    fhir_url: str | None = None
    tokens_file: str | None = None
    rate_limit_per_minute: int = 120
    max_body_bytes: int = 64 * 1024
    call_timeout_s: float = 5.0
    max_sessions: int = 100
    session_idle_timeout_s: float = 600.0
    # Simulated clinic time. None means the real system clock.
    now: datetime | None = seed.CLINIC_NOW

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        default = cls()
        now_raw = env.get("MCP_OPS_NOW", "")
        if now_raw == "system":
            now = None
        elif now_raw:
            now = datetime.fromisoformat(now_raw)
        else:
            now = default.now
        backend = env.get("MCP_OPS_BACKEND", default.backend)
        if backend not in BACKENDS:
            raise ValueError(
                f"MCP_OPS_BACKEND must be one of {', '.join(BACKENDS)}, not {backend!r}"
            )
        return cls(
            host=env.get("MCP_OPS_HOST", default.host),
            port=int(env.get("MCP_OPS_PORT", default.port)),
            backend=backend,
            db_path=env.get("MCP_OPS_DB", default.db_path),
            fhir_url=env.get("MCP_OPS_FHIR_URL") or None,
            tokens_file=env.get("MCP_OPS_TOKENS_FILE") or None,
            rate_limit_per_minute=int(
                env.get("MCP_OPS_RATE_LIMIT_PER_MIN", default.rate_limit_per_minute)
            ),
            max_body_bytes=int(env.get("MCP_OPS_MAX_BODY_BYTES", default.max_body_bytes)),
            call_timeout_s=float(env.get("MCP_OPS_CALL_TIMEOUT_S", default.call_timeout_s)),
            max_sessions=int(env.get("MCP_OPS_MAX_SESSIONS", default.max_sessions)),
            session_idle_timeout_s=float(
                env.get("MCP_OPS_SESSION_IDLE_TIMEOUT_S", default.session_idle_timeout_s)
            ),
            now=now,
        )
