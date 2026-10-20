"""Entry point for ``python -m salon_ops`` (fails closed without a tokens file)."""

from mcp_ops_kit.server import serve
from salon_ops.auth import AUTH
from salon_ops.config import ENV_PREFIX, Settings
from salon_ops.server import build_http_app


def main() -> None:
    serve(Settings.from_env(), build_http_app, auth=AUTH, env_prefix=ENV_PREFIX)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
