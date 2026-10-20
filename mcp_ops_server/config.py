"""Runtime settings. The only module that reads environment variables (see .env.example).

The shared server settings (``MCP_OPS_HOST``, ``_PORT``, ``_TOKENS_FILE``, limits, ``_NOW``)
are read by ``mcp_ops_kit``; the clinic adds its backend choice and database.
"""

import os
from dataclasses import dataclass
from datetime import datetime

from mcp_ops_kit.server import ServerSettings
from mcp_ops_server import seed

BACKENDS = ("sqlite", "fhir")
ENV_PREFIX = "MCP_OPS_"


@dataclass(frozen=True)
class Settings(ServerSettings):
    backend: str = "sqlite"  # "sqlite" or "fhir"
    db_path: str = ":memory:"
    fhir_url: str | None = None
    # Simulated clinic time. None means the real system clock.
    now: datetime | None = seed.CLINIC_NOW

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        default = cls()
        backend = env.get(f"{ENV_PREFIX}BACKEND", default.backend)
        if backend not in BACKENDS:
            raise ValueError(
                f"MCP_OPS_BACKEND must be one of {', '.join(BACKENDS)}, not {backend!r}"
            )
        return cls(
            **cls.common_from_env(ENV_PREFIX),
            backend=backend,
            db_path=env.get(f"{ENV_PREFIX}DB", default.db_path),
            fhir_url=env.get(f"{ENV_PREFIX}FHIR_URL") or None,
        )
