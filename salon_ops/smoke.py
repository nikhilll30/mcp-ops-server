"""End-to-end smoke test against a running salon server (copied from scripts/smoke_test.py).

    MCP_URL=http://127.0.0.1:8100 MCP_TOKEN=<staff token> python -m salon_ops.smoke

Checks the unauthenticated health probe, that /mcp refuses a request without a token, and a
real tool round trip: health, a stylist search, and booking then cancelling a free opening a
week out (so the server is left as it was, and the cancel is outside the 24-hour window).
Each write is confirmed by reading the openings back. Exit code 1 on any failure.
"""

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CUSTOMER = "CUS-0005"  # synthetic customer with no seeded visits
STYLIST = "STY-04"  # Jasper: offers cut
DAY = "2026-11-10"


def _check(name: str, ok: bool, detail: Any = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail != "" else ""))  # noqa: T201
    if not ok:
        raise SystemExit(1)


def _http_status(url: str, data: bytes | None = None) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 - URL from the operator
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


async def exercise(session: Any) -> None:
    """The tool round trip on an initialized MCP session. Writes are confirmed by reading
    the openings back, not by trusting the write's own response."""

    async def call(tool: str, **args: Any) -> dict[str, Any]:
        result = await session.call_tool(tool, args)
        text = result.content[0].text if result.content else ""
        _check(f"tool {tool}", not result.is_error, text if result.is_error else "")
        return result.structured_content or {}

    async def free_opening_ids() -> set[str]:
        day = await call(
            "list_openings", stylist_id=STYLIST, start_date=DAY, end_date=DAY, limit=100
        )
        return {o["opening_id"] for o in day["openings"]}

    listed = await session.list_tools()
    _check("tools listed", len(listed.tools) == 6, [t.name for t in listed.tools])
    _check("health status", (await call("health"))["status"] == "ok")
    found = await call("search_stylists", service="cut")
    _check("stylists found", len(found["stylists"]) > 0, len(found["stylists"]))

    before = await free_opening_ids()
    _check("free openings found", len(before) > 0, len(before))
    opening_id = sorted(before)[0]
    booked = await call("book_visit", customer_id=CUSTOMER, opening_id=opening_id, service="cut")
    _check(
        "booked the requested opening for the requested customer",
        booked["status"] == "booked"
        and booked["opening_id"] == opening_id
        and booked["customer_id"] == CUSTOMER,
        booked["visit_id"],
    )
    _check("opening no longer free after booking", opening_id not in await free_opening_ids())
    cancelled = await call("cancel_visit", visit_id=booked["visit_id"], customer_id=CUSTOMER)
    _check(
        "cancelled the same visit",
        cancelled["status"] == "cancelled" and cancelled["visit_id"] == booked["visit_id"],
    )
    _check("opening free again after cancelling", opening_id in await free_opening_ids())


async def _tools(base: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30)) as http:
        async with streamable_http_client(f"{base}/mcp", http_client=http) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await exercise(session)


def main() -> None:
    base = os.environ.get("MCP_URL", "http://127.0.0.1:8100").rstrip("/")
    token = os.environ.get("MCP_TOKEN", "")
    _check("MCP_TOKEN set", bool(token))
    status, body = _http_status(f"{base}/healthz")
    _check("GET /healthz", status == 200 and json.loads(body)["status"] == "ok", status)
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
    _check("POST /mcp without a token is refused", status == 401, status)
    asyncio.run(_tools(base, token))
    print("smoke test passed")  # noqa: T201


if __name__ == "__main__":
    sys.exit(main())
