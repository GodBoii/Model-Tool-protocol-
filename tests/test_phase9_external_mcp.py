"""Interoperability with the independently installed official Python MCP client."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")
mcp = pytest.importorskip("mcp")
pytestmark = pytest.mark.integration


def test_official_client_modern_stdio_discovery_and_tools(tmp_path):
    program = tmp_path / "external_server.py"
    program.write_text(
        """from mtp import MCPJsonRpcServer, ToolRegistry, ToolSpec, run_mcp_stdio
tools=ToolRegistry()
tools.register_tool(ToolSpec("echo", "Echo value", input_schema={"type":"object","properties":{"value":{"type":"integer"}},"required":["value"]}), lambda value:value)
run_mcp_stdio(MCPJsonRpcServer(tools=tools, enable_modern=True))
""",
        encoding="utf-8",
    )

    async def scenario():
        from mcp.client.stdio import StdioServerParameters, stdio_client

        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(program)],
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            },
        )
        async with stdio_client(parameters) as (reader, writer):  # noqa: SIM117
            async with mcp.ClientSession(
                reader, writer, read_timeout_seconds=5
            ) as client:
                discovered = await client.discover()
                assert "2026-07-28" in discovered.supported_versions
                assert (await client.list_tools()).tools[0].name == "echo"
                result = await client.call_tool("echo", {"value": 17})
                assert not result.is_error and result.content[0].text == "17"

    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_official_client_modern_http_discovery_and_tools():
    from mtp import (
        MCPJsonRpcServer,
        MCPStreamableHTTPTransportServer,
        ToolRegistry,
        ToolSpec,
    )

    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec(
            "echo",
            "Echo",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
            },
        ),
        lambda value: value,
    )
    transport = MCPStreamableHTTPTransportServer(
        "127.0.0.1", 0, MCPJsonRpcServer(tools=registry, enable_modern=True)
    )
    worker = threading.Thread(target=transport.start, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while transport.address is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert transport.address is not None

    async def scenario():
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(
            f"http://127.0.0.1:{transport.address[1]}/mcp"
        ) as streams, mcp.ClientSession(
            streams[0], streams[1], read_timeout_seconds=5
        ) as client:
            assert "2026-07-28" in (await client.discover()).supported_versions
            assert (await client.list_tools()).tools[0].name == "echo"
            result = await client.call_tool("echo", {"value": 42})
            assert not result.is_error and result.content[0].text == "42"

    try:
        asyncio.run(asyncio.wait_for(scenario(), 15))
    finally:
        transport.shutdown()
        worker.join(timeout=5)
