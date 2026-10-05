"""Exercise the actual stdio process while another tool request is running."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

from test_mcp_modern import request

pytestmark = pytest.mark.integration


def test_stdio_discovery_cancellation_id_reuse_and_eof(tmp_path):
    program = tmp_path / "server.py"
    program.write_text(
        """import asyncio, sys
from mtp import ToolRegistry, ToolSpec, MCPJsonRpcServer, run_mcp_stdio
tools = ToolRegistry()
async def wait():
    print("started", file=sys.stderr, flush=True)
    await asyncio.sleep(30)
    return "finished"
tools.register_tool(ToolSpec("wait", "wait", input_schema={"type":"object"}), wait)
run_mcp_stdio(MCPJsonRpcServer(tools=tools, enable_modern=True))
""",
        encoding="utf-8",
    )
    environment = dict(
        os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src")
    )
    with subprocess.Popen(
        [sys.executable, "-u", str(program)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    ) as process:

        def send(value):
            process.stdin.write(json.dumps(value) + "\n")
            process.stdin.flush()

        send(request("tools/call", "slow", name="wait"))
        send(request("server/discover", "probe"))
        response = json.loads(process.stdout.readline())
        assert response["id"] == "probe"
        assert process.stderr.readline().strip() == "started"
        send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": "slow"},
            }
        )
        send(request("ping", "barrier"))
        assert json.loads(process.stdout.readline())["id"] == "barrier"
        send(request("ping", "slow"))
        response = json.loads(process.stdout.readline())
        assert response["id"] == "slow" and "result" in response
        process.stdin.close()
        process.wait(timeout=5)
        assert process.returncode == 0
        assert not process.stdout.read()
