"""Entry point for ``python -m mcp_ops_server``."""

import os

from mcp_ops_server.server import build_server


def main() -> None:
    host = os.environ.get("MCP_OPS_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_OPS_PORT", "8000"))
    build_server().run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
