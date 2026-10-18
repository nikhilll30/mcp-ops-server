"""End-to-end checks: launch `python -m mcp_ops_server` and drive it over Streamable HTTP.

Catches failures the in-process tests cannot: a broken entry point, bad transport
arguments, env-var configuration that does not reach the server, or results and errors
that do not survive serialization to a real client.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
from conftest import DERM_PATIENT, DERM_REFERRAL, WEEK_ONE, seeded_busy_starts
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_ops_server import __version__


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            err = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(f"server exited with code {proc.returncode}:\n{err[-2000:]}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"server did not open port {port} within {timeout}s")


@pytest.fixture
def server_port() -> Iterator[int]:
    port = _free_port()
    env = {**os.environ, "MCP_OPS_HOST": "127.0.0.1", "MCP_OPS_PORT": str(port)}
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "mcp_ops_server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port, proc)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def _call_health(port: int) -> dict:
    async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "health" in {t.name for t in tools.tools}
            result = await session.call_tool("health", {})
            assert result.is_error is False
            assert result.structured_content is not None
            return result.structured_content


def test_health_over_streamable_http(server_port: int) -> None:
    content = asyncio.run(_call_health(server_port))
    assert content == {"status": "ok", "version": __version__}


async def _booking_flow(port: int) -> list[str]:
    """The task an agent will be evaluated on: find slot, check referral, book, cancel."""
    steps = []
    async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()

            async def ok(tool: str, **args: object) -> dict:
                result = await session.call_tool(tool, args)
                assert result.is_error is False, result.content
                assert result.structured_content is not None
                steps.append(tool)
                return result.structured_content

            providers = await ok("search_providers", specialty="dermatology")
            provider = providers["providers"][0]
            assert provider["requires_referral"] is True

            open_slots = await ok(
                "list_open_slots", provider_id=provider["provider_id"], start_date=WEEK_ONE
            )
            busy = seeded_busy_starts(DERM_PATIENT)
            slot = next(s for s in open_slots["slots"] if s["start"] not in busy)

            # Booking without the referral is refused, and the error reaches the client.
            refused = await session.call_tool(
                "book_appointment", {"patient_id": DERM_PATIENT, "slot_id": slot["slot_id"]}
            )
            assert refused.is_error is True
            assert "requires an approved referral" in refused.content[0].text
            steps.append("book_appointment:refused")

            referrals = await ok(
                "get_referral_status", patient_id=DERM_PATIENT, specialty="dermatology"
            )
            approved = [r for r in referrals["referrals"] if r["status"] == "approved"]
            assert [r["referral_id"] for r in approved] == [DERM_REFERRAL]

            appt = await ok(
                "book_appointment",
                patient_id=DERM_PATIENT,
                slot_id=slot["slot_id"],
                referral_id=DERM_REFERRAL,
            )
            assert appt["status"] == "booked" and appt["start"] == slot["start"]

            after = await ok(
                "list_open_slots", provider_id=provider["provider_id"], start_date=WEEK_ONE
            )
            assert slot["slot_id"] not in {s["slot_id"] for s in after["slots"]}

            cancelled = await ok(
                "cancel_appointment", appointment_id=appt["appointment_id"], patient_id=DERM_PATIENT
            )
            assert cancelled["status"] == "cancelled"

            reopened = await ok(
                "list_open_slots", provider_id=provider["provider_id"], start_date=WEEK_ONE
            )
            assert slot["slot_id"] in {s["slot_id"] for s in reopened["slots"]}
    return steps


def test_booking_flow_over_streamable_http(server_port: int) -> None:
    steps = asyncio.run(_booking_flow(server_port))
    assert steps == [
        "search_providers",
        "list_open_slots",
        "book_appointment:refused",
        "get_referral_status",
        "book_appointment",
        "list_open_slots",
        "cancel_appointment",
        "list_open_slots",
    ]
