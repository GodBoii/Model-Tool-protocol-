"""Modern requests remain independent from legacy connections and identities."""

from __future__ import annotations

import asyncio
import copy

import pytest

pytest.importorskip("jsonschema")

from mtp.mcp import MCPAuthDecision, MCPJsonRpcServer, MCPPrompt, MCPResource
from mtp.mcp_modern import (
    CLIENT_CAPABILITIES_KEY,
    CLIENT_INFO_KEY,
    MODERN_PROTOCOL_VERSION,
    PROTOCOL_VERSION_KEY,
    SERVER_INFO_KEY,
)
from mtp.protocol import ToolSpec
from mtp.runtime import ToolRegistry


def request(method="server/discover", request_id=1, **params):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {
            "_meta": {
                PROTOCOL_VERSION_KEY: MODERN_PROTOCOL_VERSION,
                CLIENT_CAPABILITIES_KEY: {},
            },
            **params,
        },
    }


def make_server(**options):
    tools = ToolRegistry()
    tools.register_tool(
        ToolSpec(
            "echo",
            "Echo",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
        lambda value: {"echo": value},
    )
    return MCPJsonRpcServer(tools=tools, enable_modern=True, **options)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_discovery_and_tools_need_no_initialize_and_do_not_mutate_legacy(asynchronous):
    server = make_server()
    handle = (
        (lambda r: asyncio.run(server.ahandle_request(r)))
        if asynchronous
        else server.handle_request
    )
    discovered = handle(request())["result"]
    assert MODERN_PROTOCOL_VERSION in discovered["supportedVersions"]
    assert discovered["capabilities"] == {"tools": {}}
    assert discovered["resultType"] == "complete"
    assert discovered["_meta"][SERVER_INFO_KEY]["name"] == "mtp-mcp-adapter"
    result = handle(request("tools/call", name="echo", arguments={"value": 17}))[
        "result"
    ]
    assert result["content"][0]["text"] == '{"echo": 17}'
    assert (
        not server.initialized
        and not server.client_initialized
        and not server.client_info
    )
    assert server.progress_events == []


@pytest.mark.parametrize("field", [PROTOCOL_VERSION_KEY, CLIENT_CAPABILITIES_KEY])
def test_metadata_required_even_after_previous_modern_request(field):
    server = make_server()
    assert "result" in server.handle_request(request())
    value = request()
    del value["params"]["_meta"][field]
    assert server.handle_request(value)["error"]["code"] == -32602


@pytest.mark.parametrize("request_id", [None, True, 1.5, [], {}])
def test_invalid_ids_fail_without_execution(request_id):
    response = make_server().handle_request(
        request("tools/call", request_id, name="echo", arguments={"value": 1})
    )
    assert response["id"] is None and response["error"]["code"] == -32600


def test_unsupported_version_reports_modern_retry_versions():
    value = request()
    value["params"]["_meta"][PROTOCOL_VERSION_KEY] = "2999-01-01"
    error = make_server().handle_request(value)["error"]
    assert error["code"] == -32022
    assert error["data"] == {
        "supported": [MODERN_PROTOCOL_VERSION],
        "requested": "2999-01-01",
    }


def test_modern_request_cannot_change_legacy_handshake():
    server = make_server()
    initialized = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "clientInfo": {"name": "legacy"},
            },
        }
    )
    assert initialized["result"]["protocolVersion"] == "2025-06-18"
    modern = request()
    modern["params"]["_meta"][CLIENT_INFO_KEY] = {"name": "modern", "version": "1"}
    server.handle_request(modern)
    assert server.client_info == {"name": "legacy"}
    assert server.handle_request(request("initialize"))["error"]["code"] == -32601


def test_auth_uses_each_request_metadata_without_identity_bleed():
    class Policy:
        def authorize(self, token, payload, context):
            return MCPAuthDecision(
                allowed=token == context.metadata.get("expectedToken")
            )

    server = make_server(auth_provider=Policy())
    for expected, supplied in [("one", "one"), ("two", "one"), ("two", "two")]:
        value = request(auth_token=supplied)
        value["params"]["_meta"]["expectedToken"] = expected
        response = server.handle_request(value)
        assert ("result" in response) == (expected == supplied)
        if "error" in response:
            assert response["error"]["code"] == 1001


@pytest.mark.parametrize(
    "arguments", [{"value": "1"}, {"value": 1, "extra": 2}, {}, None]
)
def test_argument_errors_are_invalid_params(arguments):
    response = make_server().handle_request(
        request("tools/call", name="echo", arguments=arguments)
    )
    assert response["error"]["code"] == -32602


def test_modern_ids_and_progress_do_not_share_legacy_cancellation_state():
    server = make_server()
    server.handle_request(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 1},
        }
    )
    value = request(
        "tools/call",
        name="echo",
        arguments={"value": 4},
        callId="1",
        sessionId="client-supplied",
        progressToken="same",
    )
    saved = copy.deepcopy(value)
    for _ in range(2):
        assert server.handle_request(value)["result"]["result"]["output"] == {"echo": 4}
    assert value == saved
    assert server.progress_events == []


def test_resources_prompts_and_capabilities():
    server = make_server(
        resources=[MCPResource("memory://greeting")],
        resource_reader=lambda uri: "hello",
        prompts=[MCPPrompt("greeting", template="Hello")],
    )
    assert set(server.handle_request(request())["result"]["capabilities"]) == {
        "tools",
        "resources",
        "prompts",
    }
    assert (
        server.handle_request(request("resources/read", uri="memory://greeting"))[
            "result"
        ]["contents"][0]["text"]
        == "hello"
    )
    assert (
        server.handle_request(request("prompts/get", name="greeting"))["result"][
            "messages"
        ][0]["content"]["text"]
        == "Hello"
    )


def test_schema_2020_12_local_defs_and_network_references(monkeypatch):
    from referencing.exceptions import Unresolvable

    server = make_server()
    validator = server._modern_requests._schema(
        {
            "$defs": {"value": {"type": "integer", "minimum": 5}},
            "type": "object",
            "properties": {"value": {"$ref": "#/$defs/value"}},
        }
    )
    validator.validate({"value": 8})
    from jsonschema.exceptions import ValidationError

    with pytest.raises(ValidationError):
        validator.validate({"value": 4})
    external = server._modern_requests._schema({"$ref": "http://127.0.0.1:1/forbidden"})
    with pytest.raises(Unresolvable):
        external.validate({})


def test_disabled_modern_does_not_claim_discovery():
    server = MCPJsonRpcServer(tools=ToolRegistry())
    assert not server.modern_enabled
    assert "error" in server.handle_request(request())


def test_null_params_are_malformed():
    value = request()
    value["params"] = None
    assert make_server().handle_request(value)["error"]["code"] == -32602


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_non_json_request_values_fail_before_execution(value):
    response = make_server().handle_request(
        request("tools/call", name="echo", arguments={"value": value})
    )
    assert response["error"]["code"] == -32602


def test_non_finite_tool_outputs_fail_as_internal_errors():
    tools = ToolRegistry()
    tools.register_tool(
        ToolSpec("bad_output", "bad output", input_schema={"type": "object"}),
        lambda: float("nan"),
    )
    server = MCPJsonRpcServer(tools=tools, enable_modern=True)
    response = server.handle_request(request("tools/call", name="bad_output"))
    assert response["error"]["code"] == -32603


def test_notifications_have_no_rpc_response():
    value = request("notifications/example")
    del value["id"]
    assert make_server().handle_request(value) is None


def test_modern_json_schema_keeps_literal_ref_fields_and_prefix_items():
    tools = ToolRegistry()
    tools.register_tool(
        ToolSpec(
            "raw_json",
            "raw JSON",
            input_schema={
                "type": "object",
                "properties": {
                    "value": {
                        "type": "object",
                        "properties": {"$ref": {"type": "string"}},
                    },
                    "pair": {
                        "type": "array",
                        "prefixItems": [{"type": "integer"}, {"type": "string"}],
                        "items": False,
                    },
                },
            },
        ),
        lambda value, pair: {"value": value, "pair": pair},
    )
    server = MCPJsonRpcServer(tools=tools, enable_modern=True)
    raw = {"value": {"$ref": "literal JSON field"}, "pair": [17, "a"]}
    result = server.handle_request(
        request("tools/call", name="raw_json", arguments=raw)
    )["result"]
    assert result["result"]["output"] == raw and not result["isError"]
    failed = server.handle_request(
        request("tools/call", name="raw_json", arguments={**raw, "pair": [17, "a", 3]})
    )
    assert failed["error"]["code"] == -32602
