"""Tests for the health tool."""

import asyncio

from mcp_ops_server.server import build_server


def test_list_tools_includes_health() -> None:
    tools = asyncio.run(build_server().list_tools())
    assert "health" in {t.name for t in tools}


def test_health_tool() -> None:
    result = asyncio.run(build_server().call_tool("health", {}))
    assert result.is_error is False
    content = result.structured_content
    assert content is not None
    assert content["status"] == "ok"
    assert content["version"] == "0.1.0"
