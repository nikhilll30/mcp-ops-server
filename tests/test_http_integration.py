"""End-to-end check: launch `python -m mcp_ops_server` and call `health` over Streamable HTTP.

Catches failures the in-process tests cannot: a broken entry point, bad transport
arguments, or env-var configuration that does not reach the server.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
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
            assert [t.name for t in tools.tools] == ["health"]
            result = await session.call_tool("health", {})
            assert result.is_error is False
            assert result.structured_content is not None
            return result.structured_content


def test_health_over_streamable_http(server_port: int) -> None:
    content = asyncio.run(_call_health(server_port))
    assert content == {"status": "ok", "version": __version__}
