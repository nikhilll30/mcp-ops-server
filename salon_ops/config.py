"""Runtime settings. The only module that reads environment variables (``SALON_OPS_*``).

The shared server settings (``SALON_OPS_HOST``, ``_PORT``, ``_TOKENS_FILE``, limits,
``_NOW``) are read by ``mcp_ops_kit``; the salon adds its database path.
"""

import os
from dataclasses import dataclass
from datetime import datetime

from mcp_ops_kit.server import ServerSettings
from salon_ops import seed

ENV_PREFIX = "SALON_OPS_"


@dataclass(frozen=True)
class Settings(ServerSettings):
    port: int = 8100
    db_path: str = ":memory:"
    # Simulated salon time. None means the real system clock.
    now: datetime | None = seed.SALON_NOW

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = os.environ.get(f"{ENV_PREFIX}DB", cls.db_path)
        return cls(**cls.common_from_env(ENV_PREFIX), db_path=db_path)
