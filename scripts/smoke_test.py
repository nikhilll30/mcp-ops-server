"""End-to-end smoke test against a running server, e.g. the Docker image in CI.

    MCP_URL=http://127.0.0.1:8000 MCP_TOKEN=<staff token> python scripts/smoke_test.py

Checks the unauthenticated health probe, that /mcp refuses a request without a token, and
a real tool round trip with the token: health, a provider search, and booking then
cancelling an open primary-care slot (so the server is left as it was). Exit code 1 on any
failure. The token needs all three scopes.
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

PATIENT = "PAT-0008"  # synthetic patient with no seeded bookings
PROVIDER = "PRV-001"  # primary care: no referral needed
WEEK_ONE = "2026-11-02"


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


async def _tools(base: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30)) as http:
        async with streamable_http_client(f"{base}/mcp", http_client=http) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(tool: str, **args: Any) -> dict[str, Any]:
                    result = await session.call_tool(tool, args)
                    text = result.content[0].text if result.content else ""
                    _check(f"tool {tool}", not result.is_error, text if result.is_error else "")
                    return result.structured_content or {}

                listed = await session.list_tools()
                _check("tools listed", len(listed.tools) == 6, [t.name for t in listed.tools])
                _check("health status", (await call("health"))["status"] == "ok")
                found = await call("search_providers", specialty="cardiology")
                _check("cardiologists found", len(found["providers"]) > 0, len(found["providers"]))
                slots = await call(
                    "list_open_slots", provider_id=PROVIDER, start_date=WEEK_ONE, end_date=WEEK_ONE
                )
                slot_id = slots["slots"][0]["slot_id"]
                booked = await call("book_appointment", patient_id=PATIENT, slot_id=slot_id)
                _check("booked", booked["status"] == "booked", booked["appointment_id"])
                cancelled = await call(
                    "cancel_appointment",
                    appointment_id=booked["appointment_id"],
                    patient_id=PATIENT,
                )
                _check("cancelled", cancelled["status"] == "cancelled")


def main() -> None:
    base = os.environ.get("MCP_URL", "http://127.0.0.1:8000").rstrip("/")
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
