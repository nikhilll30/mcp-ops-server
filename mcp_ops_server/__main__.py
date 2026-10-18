"""Entry point for ``python -m mcp_ops_server``."""

import os

from mcp_ops_server.server import build_server
from mcp_ops_server.store import Store


def main() -> None:
    host = os.environ.get("MCP_OPS_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_OPS_PORT", "8000"))
    # Default ":memory:" gives a fresh seeded store per process; set a file path to persist.
    store = Store(os.environ.get("MCP_OPS_DB", ":memory:"))
    build_server(store).run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
