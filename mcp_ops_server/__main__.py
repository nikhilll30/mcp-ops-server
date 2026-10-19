"""Entry point for ``python -m mcp_ops_server``."""

import sys
from pathlib import Path

import uvicorn

from mcp_ops_server.config import Settings
from mcp_ops_server.server import build_http_app


def main() -> None:
    settings = Settings.from_env()
    if not settings.tokens_file:
        # Fail closed: never serve patient-like data without auth.
        sys.exit(
            "MCP_OPS_TOKENS_FILE is not set. Issue a token first:\n"
            "  python -m mcp_ops_server.auth issue --file tokens.json --client dev "
            "--scope read:schedule --scope write:schedule --scope read:referrals\n"
            "then run with MCP_OPS_TOKENS_FILE=tokens.json"
        )
    if not Path(settings.tokens_file).is_file():
        # Same fail-closed rule, with a message instead of a traceback (e.g. a container
        # started before a tokens file was mounted).
        sys.exit(
            f"Tokens file {settings.tokens_file} not found. Issue a token into it first "
            "(python -m mcp_ops_server.auth issue --file ...) or point MCP_OPS_TOKENS_FILE at it."
        )
    app = build_http_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
