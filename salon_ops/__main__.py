"""Entry point for ``python -m salon_ops`` (copied from the clinic's ``__main__``)."""

import sys
from pathlib import Path

import uvicorn

from salon_ops.config import Settings
from salon_ops.server import build_http_app


def main() -> None:
    settings = Settings.from_env()
    if not settings.tokens_file:
        sys.exit(
            "SALON_OPS_TOKENS_FILE is not set. Issue a token first:\n"
            "  python -m salon_ops.auth issue --file tokens.json --client dev "
            "--scope read:catalog --scope write:bookings --scope read:customers\n"
            "then run with SALON_OPS_TOKENS_FILE=tokens.json"
        )
    if not Path(settings.tokens_file).is_file():
        sys.exit(
            f"Tokens file {settings.tokens_file} not found. Issue a token into it first "
            "(python -m salon_ops.auth issue --file ...) or point SALON_OPS_TOKENS_FILE at it."
        )
    uvicorn.run(build_http_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
