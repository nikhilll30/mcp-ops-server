"""Entry point for ``python -m mcp_ops_server``."""

import sys
from datetime import datetime

import uvicorn

from mcp_ops_server.auth import StaticTokenVerifier
from mcp_ops_server.config import Settings
from mcp_ops_server.server import build_server
from mcp_ops_server.store import Store


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
    fixed_now = settings.now
    store = Store(
        settings.db_path,
        timeout_s=settings.call_timeout_s,
        now=(lambda: fixed_now) if fixed_now is not None else datetime.now,
    )
    server = build_server(
        store,
        token_verifier=StaticTokenVerifier.from_file(settings.tokens_file),
        issuer_url=f"http://{settings.host}:{settings.port}",
        rate_limit_per_minute=settings.rate_limit_per_minute,
    )
    app = server.streamable_http_app(
        host=settings.host,
        max_request_body_size=settings.max_body_bytes,
        max_sessions=settings.max_sessions,
        session_idle_timeout=settings.session_idle_timeout_s,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
