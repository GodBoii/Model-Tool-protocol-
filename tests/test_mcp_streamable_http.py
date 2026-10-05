from __future__ import annotations

import base64
import http.client
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from mtp.mcp import MCPAuthDecision, MCPJsonRpcServer
from mtp.mcp_streamable_http import MCPStreamableHTTPTransportServer
from mtp.protocol import ToolSpec
from mtp.runtime import ToolRegistry

pytest.importorskip("jsonschema")
VERSION = "2026-07-28"


def request(method="tools/list", **params):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
            **params,
        },
    }


def headers(payload):
    result = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": payload["params"]["_meta"][
            "io.modelcontextprotocol/protocolVersion"
        ],
        "Mcp-Method": payload["method"],
    }
    if payload["method"] in {"tools/call", "prompts/get", "resources/read"}:
        value = payload["params"].get("name", payload["params"].get("uri"))
        result["Mcp-Name"] = value
    return result


@pytest.fixture
def serving():
    running = []

    def start(*, registry=None, **kwargs):
        server = MCPJsonRpcServer(
            tools=registry or ToolRegistry(),
            enable_modern=True,
            **kwargs.pop("server_options", {}),
        )
        transport = MCPStreamableHTTPTransportServer("127.0.0.1", 0, server, **kwargs)
        thread = threading.Thread(target=transport.start, daemon=True)
        thread.start()
        deadline = time.monotonic() + 3
        while (
            transport.address is None
            and thread.is_alive()
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        assert transport.address is not None
        running.append((transport, thread))
        return transport

    yield start
    for transport, thread in running:
        transport.shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert transport.address is None


def post(
    transport, payload, *, override=None, drop=(), path="/mcp", raw=None, method="POST"
):
    outgoing = headers(payload)
    outgoing.update(override or {})
    for name in drop:
        outgoing.pop(name)
    body = json.dumps(payload).encode() if raw is None else raw
    conn = http.client.HTTPConnection(*transport.address, timeout=3)
    try:
        conn.request(method, path, body, outgoing)
        response = conn.getresponse()
        raw_response = response.read()
        return (
            response.status,
            dict(response.getheaders()),
            json.loads(raw_response) if raw_response else None,
        )
    finally:
        conn.close()


def test_stateless_discovery_and_tools_call(serving):
    executed = []
    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec(
            "echo",
            "Echo",
            {"type": "object", "properties": {"value": {"type": "integer"}}},
        ),
        lambda value: executed.append(value) or value,
    )
    transport = serving(registry=registry)
    status, response_headers, body = post(
        transport,
        request("server/discover"),
        override={"Mcp-Session-Id": "legacy", "Last-Event-ID": "1"},
    )
    assert status == 200
    assert body["result"]["resultType"] == "complete"
    assert "Mcp-Session-Id" not in response_headers
    assert not transport.server.initialized
    status, _, body = post(
        transport, request("tools/call", name="echo", arguments={"value": 7})
    )
    assert status == 200
    assert body["result"]["isError"] is False
    assert executed == [7]
    assert not transport.server.initialized


@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT", "PATCH", "OPTIONS", "HEAD"])
def test_legacy_methods_rejected(serving, method):
    status, reply, body = post(serving(), request(), method=method)
    assert status == 405
    assert reply["Allow"] == "POST"
    assert body is None


@pytest.mark.parametrize(
    "origin", ["https://attacker.example", "null", "http://127.0.0.1:9"]
)
def test_origin_checked_for_post_and_get(serving, origin):
    transport = serving()
    for method in ["POST", "GET"]:
        assert (
            post(transport, request(), override={"Origin": origin}, method=method)[0]
            == 403
        )


def test_explicit_origin_allowlist(serving):
    transport = serving(allowed_origins={"https://trusted.example"})
    assert (
        post(transport, request(), override={"Origin": "https://trusted.example"})[0]
        == 200
    )


@pytest.mark.parametrize("drop", ["MCP-Protocol-Version", "Mcp-Method", "Mcp-Name"])
def test_required_mirrored_headers(serving, drop):
    status, _, body = post(
        serving(), request("tools/call", name="unknown", arguments={}), drop=[drop]
    )
    assert status == 400
    assert body["error"]["code"] == -32020


@pytest.mark.parametrize(
    "key,value",
    [
        ("Mcp-Method", "tools/list"),
        ("Mcp-Name", "other"),
        ("MCP-Protocol-Version", "2025-11-25"),
        ("Mcp-Name", "=?base64?bad?="),
    ],
)
def test_header_body_mismatch(serving, key, value):
    status, _, body = post(
        serving(),
        request("tools/call", name="echo", arguments={}),
        override={key: value},
    )
    assert status == 400
    assert body["error"]["code"] == -32020


def test_matching_unsupported_version_lists_modern_version(serving):
    payload = request()
    payload["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2025-11-25"
    status, _, body = post(serving(), payload)
    assert status == 400
    assert body["error"]["code"] == -32022
    assert body["error"]["data"]["supported"] == [VERSION]


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"[]", -32600),
        (b"[{}]", -32600),
        (b'{"jsonrpc":"2.0","id":1,"result":{}}', -32600),
        (b"oops", -32700),
        (b'{"value":NaN}', -32700),
    ],
)
def test_no_batch_response_or_invalid_json(serving, raw, code):
    status, _, body = post(serving(), request(), raw=raw)
    assert status == 400
    assert body["error"]["code"] == code


@pytest.mark.parametrize(
    "override,status",
    [
        ({"Content-Type": "text/plain"}, 415),
        ({"Accept": "application/json"}, 406),
        ({"Accept": "*/*"}, 406),
    ],
)
def test_media_requirements(serving, override, status):
    assert post(serving(), request(), override=override)[0] == status


def test_limits_routes_and_notifications(serving):
    transport = serving(max_body_bytes=32)
    assert post(transport, request())[0] == 413
    transport = serving()
    assert post(transport, request(), path="/rpc")[0] == 404
    payload = request("notifications/cancelled", requestId=1)
    payload.pop("id")
    status, _, body = post(transport, payload)
    assert status == 400
    assert "id" not in body
    status, _, body = post(transport, request("unrecognized/method"))
    assert status == 404
    assert body["error"]["code"] == -32601


def test_bearer_is_only_authority(serving):
    transport = serving(
        server_options={"auth_token": "real-secret", "require_auth": True}
    )
    payload = request()
    payload["params"]["auth_token"] = "real-secret"
    payload["meta"] = {"authToken": "real-secret"}
    payload["params"]["_meta"]["authToken"] = "real-secret"
    assert post(transport, payload)[0] == 401
    assert post(transport, payload, override={"Authorization": "Bearer bad"})[0] == 401
    payload["meta"]["authToken"] = "bad"
    payload["params"]["auth_token"] = "bad"
    assert (
        post(transport, payload, override={"Authorization": "bearer real-secret"})[0]
        == 200
    )


def test_async_authorizer_challenge(serving):
    class Auth:
        async def authorize(self, token, request, context):
            return MCPAuthDecision(allowed=False, www_authenticate='Bearer realm="mtp"')

    status, response_headers, _ = post(
        serving(server_options={"auth_provider": Auth()}), request()
    )
    assert status == 401
    assert response_headers["WWW-Authenticate"] == 'Bearer realm="mtp"'


def encode(value):
    return "=?base64?" + base64.b64encode(value.encode()).decode() + "?="


@pytest.mark.parametrize(
    "value", ["Hello, 世界", " padded ", "line1\nline2", "=?base64?literal?="]
)
def test_parameter_header_sentinel_decoding(serving, value):
    registry = ToolRegistry()
    calls = []
    registry.register_tool(
        ToolSpec(
            "echo",
            "Echo",
            {
                "type": "object",
                "properties": {"text": {"type": "string", "x-mcp-header": "Text"}},
            },
        ),
        lambda text: calls.append(text) or text,
    )
    transport = serving(registry=registry)
    payload = request("tools/call", name="echo", arguments={"text": value})
    assert post(transport, payload)[0] == 400
    assert calls == []
    assert (
        post(transport, payload, override={"Mcp-Param-Text": encode(value)})[0] == 200
    )
    assert calls == [value]


def test_nested_integer_and_boolean_headers(serving):
    registry = ToolRegistry()
    calls = []
    schema = {
        "type": "object",
        "properties": {
            "nested": {
                "type": "object",
                "properties": {"region": {"type": "integer", "x-mcp-header": "Region"}},
            },
            "enabled": {"type": "boolean", "x-mcp-header": "Enabled"},
        },
    }
    registry.register_tool(
        ToolSpec("echo", "Echo", schema), lambda **kw: calls.append(kw) or kw
    )
    transport = serving(registry=registry)
    payload = request(
        "tools/call",
        name="echo",
        arguments={"nested": {"region": 42}, "enabled": False},
    )
    mirrors = {"Mcp-Param-Region": "42.0", "Mcp-Param-Enabled": "false"}
    assert post(transport, payload, override=mirrors)[0] == 200
    mirrors["Mcp-Param-Enabled"] = "False"
    assert post(transport, payload, override=mirrors)[0] == 400
    assert len(calls) == 1


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "x-mcp-header": "Root"},
        {"properties": {"value": {"type": "number", "x-mcp-header": "Number"}}},
        {"properties": {"value": {"type": "string", "x-mcp-header": "Bad Name"}}},
        {
            "properties": {
                "one": {"type": "string", "x-mcp-header": "Same"},
                "two": {"type": "boolean", "x-mcp-header": "same"},
            }
        },
        {
            "oneOf": [
                {
                    "properties": {
                        "value": {"type": "string", "x-mcp-header": "Unreachable"}
                    }
                }
            ]
        },
        {
            "properties": {
                "arr": {
                    "type": "array",
                    "items": {"type": "string", "x-mcp-header": "Item"},
                }
            }
        },
    ],
)
def test_invalid_tool_annotations_hidden_and_unexecutable(serving, schema):
    registry = ToolRegistry()
    calls = []
    registry.register_tool(
        ToolSpec("bad", "Invalid header annotation", schema),
        lambda **kw: calls.append(kw),
    )
    registry.register_tool(ToolSpec("good", "Usable", {"type": "object"}), lambda: True)
    transport = serving(registry=registry)
    status, _, body = post(transport, request())
    assert status == 200
    assert [tool["name"] for tool in body["result"]["tools"]] == ["good"]
    assert post(transport, request("tools/call", name="bad", arguments={}))[0] == 400
    assert calls == []


def test_concurrent_clients_and_async_tools(serving):
    registry = ToolRegistry()

    async def echo(value):
        return value

    registry.register_tool(
        ToolSpec(
            "echo",
            "Echo",
            {"type": "object", "properties": {"value": {"type": "integer"}}},
        ),
        echo,
    )
    transport = serving(registry=registry)
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(
            pool.map(
                lambda value: post(
                    transport,
                    request("tools/call", name="echo", arguments={"value": value}),
                ),
                range(12),
            )
        )
    assert all(
        status == 200 and not body["result"]["isError"] for status, _, body in replies
    )


def test_modern_opt_in_required():
    with pytest.raises(ValueError, match="enable_modern"):
        MCPStreamableHTTPTransportServer(
            "127.0.0.1", 0, MCPJsonRpcServer(tools=ToolRegistry())
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","method":"tools/list","params":{}}',
        b'{"jsonrpc":"2.0","id":1e309,"method":"tools/list","params":{}}',
    ],
)
def test_ambiguous_and_nonfinite_json_rejected(serving, raw):
    status, _, body = post(serving(), request(), raw=raw)
    assert status == 400
    assert body["error"]["code"] == -32700


@pytest.mark.parametrize("identifier", [None, True, 1.5, 1.0, [], {}])
def test_invalid_ids_rejected(serving, identifier):
    payload = request()
    payload["id"] = identifier
    assert post(serving(), payload)[0] == 400


def test_missing_body_metadata_and_unacceptable_quality(serving):
    payload = request()
    payload["params"]["_meta"].pop("io.modelcontextprotocol/protocolVersion")
    status, _, body = post(serving(), request(), raw=json.dumps(payload).encode())
    assert status == 400
    assert body["error"]["code"] == -32602
    assert (
        post(
            serving(),
            request(),
            override={"Accept": "application/json;q=0, text/event-stream"},
        )[0]
        == 406
    )


def test_encoded_name_and_omitted_parameter_header(serving):
    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec(
            "hello世界",
            "Hello",
            {
                "type": "object",
                "properties": {"value": {"type": "string", "x-mcp-header": "Value"}},
            },
        ),
        lambda **kw: kw,
    )
    transport = serving(registry=registry)
    payload = request("tools/call", name="hello世界", arguments={})
    assert (
        post(transport, payload, override={"Mcp-Name": encode("hello世界")})[0] == 200
    )
    assert (
        post(
            transport,
            payload,
            override={"Mcp-Name": encode("hello世界"), "Mcp-Param-Value": "unexpected"},
        )[0]
        == 400
    )


@pytest.mark.parametrize(
    "duplicates", ["Mcp-Method", "MCP-Protocol-Version", "Content-Length"]
)
def test_duplicate_security_headers_rejected(serving, duplicates):
    transport = serving()
    payload = request()
    raw = json.dumps(payload).encode()
    outgoing = headers(payload)
    outgoing["Content-Length"] = str(len(raw))
    lines = ["POST /mcp HTTP/1.1", "Host: localhost"]
    lines += [f"{name}: {value}" for name, value in outgoing.items()]
    lines += [f"{duplicates}: {outgoing[duplicates]}", "Connection: close", "", ""]
    with socket.create_connection(transport.address, timeout=3) as conn:
        conn.sendall("\r\n".join(lines).encode() + raw)
        response = http.client.HTTPResponse(conn)
        response.begin()
        assert response.status == 400
        response.read()


def test_read_timeout_is_bounded(serving):
    transport = serving(read_timeout_seconds=0.05)
    with socket.create_connection(transport.address, timeout=3) as conn:
        conn.sendall(
            b"POST /mcp HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\nContent-Length: 10\r\n\r\n{"
        )
        response = http.client.HTTPResponse(conn)
        response.begin()
        assert response.status == 408
        response.read()


@pytest.mark.parametrize("value", [2**53, -(2**53), 10**400])
def test_mirrored_integer_safety_range(serving, value):
    calls = []
    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec(
            "echo",
            "Echo",
            {
                "type": "object",
                "properties": {"value": {"type": "integer", "x-mcp-header": "Value"}},
            },
        ),
        lambda value: calls.append(value),
    )
    transport = serving(registry=registry)
    status, _, body = post(
        transport,
        request("tools/call", name="echo", arguments={"value": value}),
        override={"Mcp-Param-Value": str(value)},
    )
    assert status == 400
    assert body["error"]["code"] == -32020
    assert calls == []
