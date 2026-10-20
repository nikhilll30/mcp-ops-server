"""End-to-end smoke test of a running server, e.g. a container in CI.

The kit checks what every domain's server must do: the unauthenticated ``/healthz`` probe,
a 401 for ``/mcp`` without a token, then a real MCP session with the token, in which the
domain's ``exercise(session)`` runs its own tool round trip. Exit code 1 on any failure.
"""

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

Exercise = Callable[[Any], Awaitable[None]]


def check(name: str, ok: bool, detail: Any = "") -> None:  # noqa: ANN401 - any printable
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail != "" else ""))  # noqa: T201
    if not ok:
        raise SystemExit(1)


async def call(session: Any, tool: str, **args: Any) -> dict[str, Any]:  # noqa: ANN401
    """Call ``tool``; any error fails the smoke test. Returns the structured result."""
    result = await session.call_tool(tool, args)
    text = result.content[0].text if result.content else ""
    check(f"tool {tool}", not result.is_error, text if result.is_error else "")
    return result.structured_content or {}


def _http_status(url: str, data: bytes | None = None) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 - URL from the operator
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


async def _tools(base: str, token: str, exercise: Exercise) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30)) as http:
        async with streamable_http_client(f"{base}/mcp", http_client=http) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await exercise(session)


def main(exercise: Exercise, default_url: str) -> None:
    """Read ``MCP_URL`` (default ``default_url``) and ``MCP_TOKEN``, then run every check."""
    base = os.environ.get("MCP_URL", default_url).rstrip("/")
    token = os.environ.get("MCP_TOKEN", "")
    check("MCP_TOKEN set", bool(token))
    status, body = _http_status(f"{base}/healthz")
    check("GET /healthz", status == 200 and json.loads(body)["status"] == "ok", status)
    init = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0"},
            },
        }
    ).encode()
    status, _ = _http_status(f"{base}/mcp", init)
    check("POST /mcp without a token is refused", status == 401, status)
    asyncio.run(_tools(base, token, exercise))
    print("smoke test passed")  # noqa: T201


if __name__ == "__main__":
    sys.exit("Run a domain's smoke test, e.g. python scripts/smoke_test.py")
