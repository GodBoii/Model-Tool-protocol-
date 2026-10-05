"""Modern feature contracts through loopback HTTP and independent requests."""

from __future__ import annotations

import asyncio
import copy
import http.client
import io
import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("jsonschema")

from test_mcp_streamable_http import headers, post, request
from test_mcp_streamable_http import serving as _serving

from mtp.mcp import MCPJsonRpcServer
from mtp.mcp_features import (
    MCPInputRequired,
    MCPOAuthMetadata,
    ModernMCPFeatures,
    configure_modern_mcp,
    current_mcp_context,
)
from mtp.protocol import ToolSpec
from mtp.runtime import ToolRegistry

serving = _serving


def features(transport, **kwargs):
    features = ModernMCPFeatures(**kwargs)
    configure_modern_mcp(transport.server, features)
    return features


def stream(transport, payload, token=None):
    conn = http.client.HTTPConnection(*transport.address, timeout=3)
    outgoing = headers(payload)
    if token:
        outgoing["Authorization"] = "Bearer " + token
    conn.request("POST", "/mcp", json.dumps(payload), outgoing)
    response = conn.getresponse()
    assert response.status == 200
    assert response.headers["Content-Type"] == "text/event-stream"
    return conn, response


def frame(response):
    while line := response.readline():
        if line.startswith(b"data: "):
            return json.loads(line[6:])
    return None


def with_progress(payload, token="progress"):
    payload["params"]["_meta"]["progressToken"] = token
    return payload


def test_request_scoped_progress_then_final_response(serving):
    registry = ToolRegistry()

    async def slow(value):
        context = current_mcp_context()
        context.progress(1, total=2, message="first")
        await asyncio.sleep(0.05)
        context.progress(2, total=2)
        return value

    registry.register_tool(ToolSpec("slow", "Slow", {"type": "object"}), slow)
    transport = serving(registry=registry, sse_response=True)
    conn, response = stream(
        transport,
        with_progress(request("tools/call", name="slow", arguments={"value": 7})),
    )
    try:
        first, second, last = frame(response), frame(response), frame(response)
        assert [first["params"]["progress"], second["params"]["progress"]] == [1, 2]
        assert first["params"]["progressToken"] == "progress"
        assert last["result"]["content"][0]["text"] == "7"
        assert frame(response) is None
    finally:
        response.close()
        conn.close()
    assert transport.server.progress_events == []


def test_stream_disconnect_cancels_async_tool(serving):
    cancelled = threading.Event()
    started = threading.Event()
    registry = ToolRegistry()

    async def slow():
        started.set()
        current_mcp_context().progress(1)
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    registry.register_tool(ToolSpec("slow", "Slow", {"type": "object"}), slow)
    transport = serving(registry=registry, sse_response=True)
    conn, response = stream(
        transport, with_progress(request("tools/call", name="slow", arguments={}))
    )
    assert frame(response)["method"] == "notifications/progress"
    assert started.is_set()
    response.close()
    conn.close()
    assert cancelled.wait(2)


def test_concurrent_progress_tokens_do_not_cross_clients(serving):
    registry = ToolRegistry()

    async def echo(value):
        context = current_mcp_context()
        context.progress(1, message=str(value))
        await asyncio.sleep(0.05)
        return value

    registry.register_tool(ToolSpec("echo", "Echo", {"type": "object"}), echo)
    transport = serving(registry=registry, sse_response=True)

    def run(value):
        payload = with_progress(
            request("tools/call", name="echo", arguments={"value": value}), str(value)
        )
        conn, response = stream(transport, payload)
        try:
            return frame(response), frame(response)
        finally:
            response.close()
            conn.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(run, range(4)))
    for value, (notification, final) in enumerate(replies):
        assert notification["params"]["progressToken"] == str(value)
        assert notification["params"]["message"] == str(value)
        assert final["result"]["content"][0]["text"] == str(value)


def test_subscriptions_ack_filter_and_principal_isolation(serving):
    transport = serving(
        sse_response=True,
        server_options={
            "auth_validator": lambda token, request: token in {"alice", "bob"}
        },
    )
    configured = features(transport)
    alice = request("subscriptions/listen", notifications={"toolsListChanged": True})
    bob = request("subscriptions/listen", notifications={"promptsListChanged": True})
    bob["id"] = 2
    alice_conn, alice_response = stream(transport, alice, "alice")
    bob_conn, bob_response = stream(transport, bob, "bob")
    try:
        assert frame(alice_response)["params"]["notifications"] == {
            "toolsListChanged": True
        }
        assert (
            frame(bob_response)["params"]["_meta"][
                "io.modelcontextprotocol/subscriptionId"
            ]
            == 2
        )
        assert (
            configured.publish("notifications/tools/list_changed", auth_token="bob")
            == 0
        )
        assert (
            configured.publish("notifications/tools/list_changed", auth_token="alice")
            == 1
        )
        assert frame(alice_response)["method"] == "notifications/tools/list_changed"
        assert (
            configured.publish("notifications/prompts/list_changed", auth_token="bob")
            == 1
        )
        assert frame(bob_response)["method"] == "notifications/prompts/list_changed"
        configured.close()
        assert (
            frame(alice_response)["result"]["_meta"][
                "io.modelcontextprotocol/subscriptionId"
            ]
            == 1
        )
        assert frame(bob_response)["result"]["resultType"] == "complete"
    finally:
        alice_response.close()
        bob_response.close()
        alice_conn.close()
        bob_conn.close()
    assert configured._subscriptions == {}


def interaction_server(serving, **limits):
    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec("ask", "Ask", {"type": "object"}),
        lambda: pytest.fail("Original tool must not run before input"),
    )
    transport = serving(
        registry=registry,
        server_options={
            "auth_validator": lambda token, request: token in {"alice", "bob"}
        },
    )
    configured = features(transport, **limits)
    executions = []

    def prepare(params, context):
        def resume(responses, context):
            executions.append(responses["root"]["roots"])
            return {"content": [{"type": "text", "text": "done"}], "isError": False}

        return MCPInputRequired(
            {"root": {"method": "roots/list", "params": {}}}, resume
        )

    configured.register_interaction("tools/call", "ask", prepare)
    payload = request("tools/call", name="ask", arguments={})
    payload["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {
        "roots": {}
    }
    return transport, configured, executions, payload


def test_mrtr_resume_exactly_once_and_request_binding(serving):
    transport, _, executions, payload = interaction_server(serving)
    status, _, first = post(
        transport, payload, override={"Authorization": "Bearer alice"}
    )
    assert status == 200
    assert first["result"]["resultType"] == "input_required"
    retry = copy.deepcopy(payload)
    retry["id"] = 2
    retry["params"].update(
        requestState=first["result"]["requestState"],
        inputResponses={"root": {"roots": [{"uri": "file:///project"}]}},
    )
    assert post(transport, retry, override={"Authorization": "Bearer bob"})[0] == 400
    changed = copy.deepcopy(retry)
    changed["params"]["arguments"] = {"changed": True}
    assert (
        post(transport, changed, override={"Authorization": "Bearer alice"})[0] == 400
    )
    assert executions == []
    assert (
        post(transport, retry, override={"Authorization": "Bearer alice"})[2]["result"][
            "resultType"
        ]
        == "complete"
    )
    retry["id"] = 3
    assert post(transport, retry, override={"Authorization": "Bearer alice"})[0] == 200
    assert len(executions) == 1
    retry["params"]["inputResponses"]["root"]["roots"] = []
    assert post(transport, retry, override={"Authorization": "Bearer alice"})[0] == 400
    assert len(executions) == 1


def test_mrtr_missing_capability_missing_input_expiry_and_capacity(serving):
    transport, _, executions, payload = interaction_server(
        serving, max_states=1, state_ttl_seconds=0.1
    )
    missing = copy.deepcopy(payload)
    missing["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {}
    status, _, error = post(
        transport, missing, override={"Authorization": "Bearer alice"}
    )
    assert status == 400 and error["error"]["code"] == -32021
    first = post(transport, payload, override={"Authorization": "Bearer alice"})[2][
        "result"
    ]
    assert (
        post(transport, payload, override={"Authorization": "Bearer alice"})[0] == 400
    )
    retry = copy.deepcopy(payload)
    retry["id"] = 2
    retry["params"]["requestState"] = first["requestState"]
    assert (
        post(transport, retry, override={"Authorization": "Bearer alice"})[2]["result"][
            "resultType"
        ]
        == "input_required"
    )
    time.sleep(0.11)
    retry["params"]["inputResponses"] = {"root": {"roots": []}}
    assert post(transport, retry, override={"Authorization": "Bearer alice"})[0] == 400
    assert executions == []


@pytest.mark.parametrize(
    "method,params,capability,response",
    [
        (
            "sampling/createMessage",
            {"messages": [], "maxTokens": 10},
            {"sampling": {}},
            {
                "role": "assistant",
                "model": "test",
                "content": {"type": "text", "text": "yes"},
            },
        ),
        (
            "elicitation/create",
            {
                "mode": "form",
                "message": "Name?",
                "requestedSchema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {"elicitation": {"form": {}}},
            {"action": "accept", "content": {"name": "tester"}},
        ),
    ],
)
def test_sampling_and_elicitation_callbacks(
    serving, method, params, capability, response
):
    transport, configured, _, payload = interaction_server(serving)
    configured.register_interaction(
        "tools/call",
        "ask",
        lambda params_, context: MCPInputRequired(
            {"input": {"method": method, "params": params}},
            lambda responses, context: {
                "content": [{"type": "text", "text": "complete"}]
            },
        ),
    )
    payload["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = (
        capability
    )
    first = post(transport, payload, override={"Authorization": "Bearer alice"})[2][
        "result"
    ]
    payload["id"] = 2
    payload["params"].update(
        requestState=first["requestState"], inputResponses={"input": response}
    )
    assert (
        post(transport, payload, override={"Authorization": "Bearer alice"})[2][
            "result"
        ]["resultType"]
        == "complete"
    )


def test_oauth_discovery_and_challenge_are_explicit(serving):
    metadata = MCPOAuthMetadata(
        "https://resource.example/mcp",
        ["https://identity.example"],
        ["read"],
        {
            "issuer": "https://identity.example",
            "authorization_endpoint": "https://identity.example/authorize",
            "token_endpoint": "https://identity.example/token",
        },
    )
    transport = serving(oauth_metadata=metadata, server_options={"auth_token": "valid"})
    status, response_headers, _ = post(transport, request())
    assert status == 401
    assert (
        'resource_metadata="https://resource.example/.well-known/oauth-protected-resource/mcp"'
        in response_headers["WWW-Authenticate"]
    )
    for path, field in [
        (metadata.metadata_path, "resource"),
        ("/.well-known/oauth-authorization-server", "issuer"),
    ]:
        status, _, body = post(transport, request(), method="GET", path=path)
        assert status == 200 and field in body
    assert (
        post(
            transport,
            request(),
            method="GET",
            path=metadata.metadata_path,
            override={"Origin": "https://attacker.example"},
        )[0]
        == 403
    )
    assert (
        post(transport, request(), override={"Authorization": "Bearer valid"})[0] == 200
    )


def test_feature_configuration_rejects_unverified_defaults(serving):
    with pytest.raises(ValueError, match="authorizer"):
        serving(
            oauth_metadata=MCPOAuthMetadata(
                "https://resource.example/mcp", ["https://issuer.example"]
            )
        )
    with pytest.raises(ValueError, match="enable_modern"):
        configure_modern_mcp(
            MCPJsonRpcServer(tools=ToolRegistry()), ModernMCPFeatures()
        )


def test_mrtr_concurrent_resume_executes_registry_handler_once(serving):
    executions = []
    entered = threading.Event()
    release = threading.Event()
    registry = ToolRegistry()

    async def approved():
        executions.append(1)
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return "approved"

    registry.register_tool(ToolSpec("ask", "Ask", {"type": "object"}), approved)
    transport = serving(registry=registry)
    configured = features(transport)
    configured.register_interaction(
        "tools/call",
        "ask",
        lambda params, context: MCPInputRequired(
            {"root": {"method": "roots/list", "params": {}}},
            lambda responses, ctx: ctx.execute_tool(),
        ),
    )
    payload = request("tools/call", name="ask", arguments={})
    payload["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {
        "roots": {}
    }
    first = post(transport, payload)[2]["result"]
    payload["id"] = 2
    payload["params"].update(
        requestState=first["requestState"], inputResponses={"root": {"roots": []}}
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        original = pool.submit(post, transport, payload)
        assert entered.wait(2)
        second = copy.deepcopy(payload)
        second["id"] = 3
        assert post(transport, second)[0] == 400
        release.set()
        assert (
            original.result(timeout=2)[2]["result"]["content"][0]["text"] == "approved"
        )
    assert post(transport, second)[0] == 200
    assert executions == [1]


def test_stream_auth_fails_before_start_and_capacity_is_bounded(serving):
    transport = serving(sse_response=True, server_options={"auth_token": "valid"})
    assert post(transport, with_progress(request()))[0] == 401
    transport = serving(sse_response=True, max_streams=1)
    features(transport)
    conn, response = stream(
        transport, request("subscriptions/listen", notifications={})
    )
    try:
        assert frame(response)["method"] == "notifications/subscriptions/acknowledged"
        assert (
            post(transport, request("subscriptions/listen", notifications={}))[0] == 503
        )
    finally:
        response.close()
        conn.close()


def test_subscription_disconnect_and_server_shutdown_release_state(serving):
    transport = serving(sse_response=True)
    configured = features(transport)
    conn, response = stream(
        transport,
        request(
            "subscriptions/listen",
            notifications={"resourceSubscriptions": ["file:///one"]},
        ),
    )
    assert frame(response)["params"]["notifications"] == {
        "resourceSubscriptions": ["file:///one"]
    }
    assert (
        configured.publish("notifications/resources/updated", {"uri": "file:///other"})
        == 0
    )
    assert (
        configured.publish("notifications/resources/updated", {"uri": "file:///one"})
        == 1
    )
    assert frame(response)["params"]["uri"] == "file:///one"
    response.close()
    conn.close()
    deadline = time.monotonic() + 2
    while configured._subscriptions and time.monotonic() < deadline:
        time.sleep(0.01)
    assert configured._subscriptions == {}
    conn, response = stream(
        transport, request("subscriptions/listen", notifications={})
    )
    assert frame(response)["method"] == "notifications/subscriptions/acknowledged"
    transport.shutdown()
    try:
        assert frame(response)["result"]["resultType"] == "complete"
    finally:
        response.close()
        conn.close()


def test_official_client_accepts_progress_subscription_and_mrtr(serving):
    mcp = pytest.importorskip("mcp")
    import mcp_types as types

    registry = ToolRegistry()

    async def echo(value):
        current_mcp_context().progress(1, total=1)
        return value

    registry.register_tool(ToolSpec("echo", "Echo", {"type": "object"}), echo)
    registry.register_tool(ToolSpec("ask", "Ask", {"type": "object"}), lambda: "answer")
    transport = serving(registry=registry, sse_response=True)
    configured = features(transport)
    configured.register_interaction(
        "tools/call",
        "ask",
        lambda params, context: MCPInputRequired(
            {"root": {"method": "roots/list", "params": {}}},
            lambda responses, ctx: ctx.execute_tool(),
        ),
    )

    async def scenario():
        from mcp.client.streamable_http import streamable_http_client
        from mcp.client.subscriptions import listen

        async def roots(context):
            return types.ListRootsResult(roots=[])

        seen = []

        async def progress(value, total, message):
            seen.append(value)

        async with (
            streamable_http_client(
                f"http://127.0.0.1:{transport.address[1]}/mcp"
            ) as streams,
            mcp.ClientSession(
                streams[0],
                streams[1],
                read_timeout_seconds=3,
                list_roots_callback=roots,
            ) as client,
        ):
            await client.discover()
            assert (
                await client.call_tool(
                    "echo", {"value": 17}, progress_callback=progress
                )
            ).content[0].text == "17"
            assert seen == [1]
            required = await client.call_tool("ask", {}, allow_input_required=True)
            assert required.result_type == "input_required"
            complete = await client.call_tool(
                "ask",
                {},
                request_state=required.request_state,
                input_responses={"root": types.ListRootsResult(roots=[])},
            )
            assert complete.content[0].text == "answer"
            async with listen(client, tools_list_changed=True) as subscription:
                assert configured.publish("notifications/tools/list_changed") == 1
                event = await anext(subscription)
                assert event is not None

    asyncio.run(asyncio.wait_for(scenario(), 12))


def test_mrtr_registry_helper_retains_approval_denial(serving):
    from mtp.protocol import ToolRiskLevel

    executions = []
    registry = ToolRegistry(approval_handler=lambda spec, call, arguments: False)
    registry.register_tool(
        ToolSpec(
            "ask", "Ask", {"type": "object"}, risk_level=ToolRiskLevel.DESTRUCTIVE
        ),
        lambda: executions.append(1),
    )
    transport = serving(registry=registry)
    configured = features(transport)
    configured.register_interaction(
        "tools/call",
        "ask",
        lambda params, context: MCPInputRequired(
            {"root": {"method": "roots/list", "params": {}}},
            lambda responses, ctx: ctx.execute_tool(),
        ),
    )
    payload = request("tools/call", name="ask", arguments={})
    payload["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {
        "roots": {}
    }
    first = post(transport, payload)[2]["result"]
    payload["id"] = 2
    payload["params"].update(
        requestState=first["requestState"], inputResponses={"root": {"roots": []}}
    )
    complete = post(transport, payload)[2]["result"]
    assert complete["isError"] is True
    assert complete["result"]["approval"] == "ask"
    assert complete["result"]["success"] is False
    assert executions == []


def test_oauth_permission_challenge_status_and_unsafe_metadata(serving):
    from mtp.mcp import MCPAuthDecision

    class ScopeAuthorizer:
        def authorize(self, token, payload, context):
            return MCPAuthDecision(
                allowed=False,
                error_code=1500,
                details={"http_status": 403},
                www_authenticate='Bearer error="insufficient_scope", scope="files:write"',
            )

    metadata = MCPOAuthMetadata(
        "https://resource.example/mcp", ["https://issuer.example"]
    )
    transport = serving(
        sse_response=True,
        oauth_metadata=metadata,
        server_options={"auth_provider": ScopeAuthorizer()},
    )
    for payload in [request(), with_progress(request())]:
        status, outgoing, body = post(transport, payload)
        assert status == 403
        assert body["error"]["code"] == 1500
        assert "insufficient_scope" in outgoing["WWW-Authenticate"]
        assert "resource_metadata=" in outgoing["WWW-Authenticate"]
    for url in [
        'https://resource.example/mcp"bad',
        "http://remote.example/mcp",
        "https://secret@resource.example/mcp",
    ]:
        with pytest.raises(ValueError):
            MCPOAuthMetadata(url, ["https://issuer.example"])


def test_mrtr_invalid_form_content_never_calls_continuation(serving):
    transport, configured, executions, payload = interaction_server(serving)
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    configured.register_interaction(
        "tools/call",
        "ask",
        lambda params, context: MCPInputRequired(
            {
                "form": {
                    "method": "elicitation/create",
                    "params": {
                        "mode": "form",
                        "message": "Name",
                        "requestedSchema": schema,
                    },
                }
            },
            lambda responses, ctx: executions.append(1) or {"content": []},
        ),
    )
    payload["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {
        "elicitation": {"form": {}}
    }
    first = post(transport, payload, override={"Authorization": "Bearer alice"})[2][
        "result"
    ]
    payload["id"] = 2
    payload["params"].update(
        requestState=first["requestState"],
        inputResponses={"form": {"action": "accept", "content": {"name": 123}}},
    )
    assert (
        post(transport, payload, override={"Authorization": "Bearer alice"})[0] == 400
    )
    assert executions == []


def test_stdio_completed_context_cannot_publish_after_id_reuse():
    from mtp.mcp_stdio_modern import serve_modern_stdio

    class Reader:
        def __init__(self):
            self.lines = queue.Queue()

        def readline(self):
            return self.lines.get(timeout=3)

    async def scenario():
        contexts = []
        second_started = asyncio.Event()
        registry = ToolRegistry()

        async def echo(value):
            if value == 1:
                contexts.append(current_mcp_context())
                return value
            second_started.set()
            await asyncio.Event().wait()

        registry.register_tool(ToolSpec("echo", "Echo", {"type": "object"}), echo)
        server = MCPJsonRpcServer(tools=registry, enable_modern=True)
        reader, writer = Reader(), io.StringIO()
        task = asyncio.create_task(serve_modern_stdio(server, reader, writer))
        try:
            first = with_progress(
                request("tools/call", name="echo", arguments={"value": 1}), "old"
            )
            reader.lines.put(json.dumps(first) + "\n")
            while not writer.getvalue():
                await asyncio.sleep(0.005)
            assert json.loads(writer.getvalue())["result"]["content"][0]["text"] == "1"
            second = with_progress(
                request("tools/call", name="echo", arguments={"value": 2}), "new"
            )
            reader.lines.put(json.dumps(second) + "\n")
            await second_started.wait()
            contexts[0].progress(1, message="late old request")
            assert len(writer.getvalue().splitlines()) == 1
        finally:
            reader.lines.put("")
            await task

    asyncio.run(asyncio.wait_for(scenario(), 5))
