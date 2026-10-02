"""Loopback transport audit. These tests never bind to a public interface."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from urllib.request import Request, urlopen

import pytest

from mtp.mcp import MCPJsonRpcServer
from mtp.mcp_transport import MCPHTTPTransportServer, MCPWebSocketTransportServer
from mtp.protocol import ToolSpec
from mtp.runtime import ToolRegistry
from mtp.schema import MessageEnvelope
from mtp.transport.http import HTTPTransportServer
from mtp.transport.ws import WebSocketTransportServer

pytestmark = pytest.mark.integration


def registry():
    result = ToolRegistry()
    result.register_tool(
        ToolSpec(
            "echo",
            "Echo a value",
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
        lambda value: value,
    )
    return result


def rpc(method, params=None, request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


@contextmanager
def serving_http(transport, server_attribute):
    errors = []

    def start():
        try:
            transport.start()
        except Exception as exc:  # noqa: BLE001 - propagate startup failures back to the test thread
            errors.append(exc)

    thread = threading.Thread(target=start, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while getattr(transport, server_attribute) is None and not errors:
        if time.monotonic() > deadline:
            raise TimeoutError("Loopback test server did not start")
        time.sleep(0.01)
    if errors:
        raise errors[0]
    server = getattr(transport, server_attribute)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        transport.shutdown()
        server.server_close()
        thread.join(timeout=3)


def post(url, payload, headers=None):
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(request, timeout=3) as response:
        body = response.read()
        return response.status, json.loads(body) if body else None


def test_http_envelope_round_trip_and_invalid_body():
    transport = HTTPTransportServer(
        "127.0.0.1",
        0,
        lambda envelope: MessageEnvelope.create("result", envelope.payload),
    )
    with serving_http(transport, "_server") as url:
        status, result = post(
            url, MessageEnvelope.create("request", {"value": "synthetic"}).to_dict()
        )
        assert status == 200 and result["payload"] == {"value": "synthetic"}
        from urllib.error import HTTPError

        with pytest.raises(HTTPError) as error:
            post(url, [])
        assert error.value.code == 400


def test_stdio_process_handles_envelopes_and_invalid_json():
    code = (
        "from mtp.transport.stdio import run_stdio_transport; from mtp.schema import MessageEnvelope; "
        "run_stdio_transport(lambda e: MessageEnvelope.create('result', e.payload))"
    )
    payload = (
        MessageEnvelope.create("request", {"value": "synthetic"}).to_json()
        + "\n{invalid\n"
    )
    process = subprocess.run(
        [sys.executable, "-c", code],
        input=payload,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    results = [json.loads(line) for line in process.stdout.splitlines()]
    assert results[0]["payload"] == {"value": "synthetic"}
    assert results[1]["kind"] == "error"


def test_websocket_envelope_round_trip():
    websockets = pytest.importorskip("websockets")

    async def probe():
        transport = WebSocketTransportServer(
            "127.0.0.1",
            0,
            lambda envelope: MessageEnvelope.create("result", envelope.payload),
        )
        await transport.start()
        try:
            port = transport._server.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                await client.send(
                    MessageEnvelope.create("request", {"value": "synthetic"}).to_json()
                )
                result = json.loads(await asyncio.wait_for(client.recv(), 3))
                assert result["payload"] == {"value": "synthetic"}
        finally:
            await transport.shutdown()

    asyncio.run(probe())


def test_mcp_http_lifecycle_and_tool_round():
    server = MCPJsonRpcServer(tools=registry())
    transport = MCPHTTPTransportServer("127.0.0.1", 0, server)
    with serving_http(transport, "_http") as url:
        status, response = post(
            url, rpc("initialize", {"protocolVersion": server.protocol_version})
        )
        assert (
            status == 200
            and response["result"]["protocolVersion"] == server.protocol_version
        )
        post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _, listing = post(url, rpc("tools/list", request_id=2))
        assert listing["result"]["tools"][0]["name"] == "echo"
        _, result = post(
            url,
            rpc("tools/call", {"name": "echo", "arguments": {"value": "synthetic"}}, 3),
        )
        assert result["result"]["isError"] is False
        assert "synthetic" in result["result"]["content"][0]["text"]


def test_mcp_websocket_lifecycle_and_tool_round():
    websockets = pytest.importorskip("websockets")

    async def probe():
        server = MCPJsonRpcServer(tools=registry(), support_progress=False)
        transport = MCPWebSocketTransportServer("127.0.0.1", 0, server)
        await transport.start()
        try:
            port = transport._server.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                await client.send(
                    json.dumps(
                        rpc("initialize", {"protocolVersion": server.protocol_version})
                    )
                )
                response = json.loads(await asyncio.wait_for(client.recv(), 3))
                assert response["result"]["protocolVersion"] == server.protocol_version
                await client.send(
                    json.dumps(
                        {"jsonrpc": "2.0", "method": "notifications/initialized"}
                    )
                )
                await client.send(
                    json.dumps(
                        rpc(
                            "tools/call",
                            {"name": "echo", "arguments": {"value": "synthetic"}},
                            2,
                        )
                    )
                )
                result = json.loads(await asyncio.wait_for(client.recv(), 3))
                assert result["id"] == 2 and result["result"]["isError"] is False
        finally:
            await transport.shutdown()

    asyncio.run(probe())


def test_mcp_does_not_claim_an_unknown_protocol_version():
    server = MCPJsonRpcServer(tools=registry())
    response = server.handle_request(
        rpc("initialize", {"protocolVersion": "9999-01-01"})
    )
    assert response["result"]["protocolVersion"] != "9999-01-01"


def test_mcp_http_rejects_untrusted_origin():
    from urllib.error import HTTPError

    transport = MCPHTTPTransportServer(
        "127.0.0.1", 0, MCPJsonRpcServer(tools=registry())
    )
    with serving_http(transport, "_http") as url:
        with pytest.raises(HTTPError) as error:
            post(url, rpc("initialize"), {"Origin": "https://untrusted.example"})
        assert error.value.code == 403


def test_mcp_unknown_method_returns_jsonrpc_method_not_found():
    server = MCPJsonRpcServer(tools=registry())
    server.handle_request(rpc("initialize"))
    response = server.handle_request(rpc("audit/unknown"))
    assert response["error"]["code"] == -32601
