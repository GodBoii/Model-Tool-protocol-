from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from mtp.agent import AgentAction
from mtp.protocol import ToolSpec
from mtp.providers.azure_provider import (
    AzureOpenAIResponsesToolCallingProvider,
    azure_v1_endpoint,
)
from mtp.providers.bedrock_provider import BedrockConverseToolCallingProvider
from mtp.providers.vertex_provider import VertexGeminiToolCallingProvider
from mtp.providers.xai_provider import XAIResponsesToolCallingProvider
from mtp.runtime import ToolRegistry

TOOLS = [
    ToolSpec(
        name="calculator.add",
        description="Add numbers",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    )
]
MESSAGES = [
    {"role": "system", "content": "Use tools"},
    {"role": "user", "content": "2+3"},
]


def _responses_reply():
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ok"}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }


@pytest.mark.parametrize(
    "klass, config, provider",
    [
        (
            AzureOpenAIResponsesToolCallingProvider,
            {"model": "my-deployment", "endpoint": "https://example.openai.azure.com"},
            "azure_openai",
        ),
        (XAIResponsesToolCallingProvider, {"model": "grok-explicit"}, "xai"),
    ],
)
def test_response_hosts_and_provider_labels(klass, config, provider):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return _responses_reply()

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    adapter = klass(client=client, **config)
    action = adapter.next_action(MESSAGES, TOOLS)
    assert action.response_text == "ok"
    assert action.metadata["provider"] == provider
    assert adapter.capabilities().provider == provider
    assert requests[0]["model"] == config["model"]
    assert requests[0]["store"] is False
    assert requests[0]["include"] == ["reasoning.encrypted_content"]
    assert requests[0]["tools"][0]["strict"] is False


@pytest.mark.parametrize(
    "endpoint, expected",
    [
        (
            "https://resource.openai.azure.com",
            "https://resource.openai.azure.com/openai/v1",
        ),
        (
            "https://resource.services.ai.azure.com/openai/v1/",
            "https://resource.services.ai.azure.com/openai/v1",
        ),
    ],
)
def test_azure_endpoint_normalization(endpoint, expected):
    assert azure_v1_endpoint(endpoint) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://resource.openai.azure.com/openai/deployments/test",
        "https://resource.openai.azure.com/openai/v1?api-key=secret",
        "http://resource.openai.azure.com",
        "https://user:secret@resource.openai.azure.com",
    ],
)
def test_azure_invalid_endpoint(endpoint):
    with pytest.raises(ValueError):
        azure_v1_endpoint(endpoint)


@pytest.mark.parametrize(
    "config",
    [
        {"model": ""},
        {"model": "deployment", "api_key": "synthetic", "use_entra": True},
        {"model": "deployment", "api_version": "2025-01-01"},
    ],
)
def test_azure_invalid_configuration(config):
    with pytest.raises(ValueError):
        AzureOpenAIResponsesToolCallingProvider(
            endpoint="https://example.openai.azure.com", client=object(), **config
        )


def test_xai_does_not_use_openai_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    with pytest.raises(ValueError, match="XAI_API_KEY"):
        XAIResponsesToolCallingProvider(model="grok-explicit")


@pytest.mark.parametrize(
    "klass, config",
    [
        (
            AzureOpenAIResponsesToolCallingProvider,
            {"model": "deployment", "endpoint": "https://resource.openai.azure.com"},
        ),
        (XAIResponsesToolCallingProvider, {"model": "grok-explicit"}),
    ],
)
@pytest.mark.parametrize("key", [True, "", " "])
def test_response_api_key_boundary(klass, config, key):
    with pytest.raises(ValueError, match="nonempty string"):
        klass(client=object(), api_key=key, **config)


def test_azure_async_token_callback_rejected_at_boundary():
    async def token():
        return "synthetic-token"

    with pytest.raises(TypeError, match="synchronous"):
        AzureOpenAIResponsesToolCallingProvider(
            model="deployment",
            endpoint="https://resource.openai.azure.com",
            client=object(),
            token_provider=token,
        )


@pytest.mark.integration
def test_azure_sdk_v1_path_and_token_callback_refresh(monkeypatch):
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx2")
    requests = []
    refreshes = []

    def token():
        refreshes.append(1)
        return f"synthetic-token-{len(refreshes)}"

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "response",
                "created_at": 1,
                "model": "deployment",
                **_responses_reply(),
            },
        )

    original = openai.OpenAI

    def factory(**kwargs):
        return original(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        )

    monkeypatch.setattr(openai, "OpenAI", factory)
    provider = AzureOpenAIResponsesToolCallingProvider(
        model="deployment",
        endpoint="https://resource.openai.azure.com",
        token_provider=token,
    )
    for _ in range(2):
        assert provider.next_action(MESSAGES, TOOLS).response_text == "ok"
    assert [str(request.url) for request in requests] == [
        "https://resource.openai.azure.com/openai/v1/responses"
    ] * 2
    assert requests[0].headers["authorization"] != requests[1].headers["authorization"]
    assert len(refreshes) == 2
    assert json.loads(requests[0].content)["model"] == "deployment"
    asyncio.run(provider.aclose())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_azure_native_async_refreshes_awaitable_token(monkeypatch):
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx2")
    requests = []
    refreshes = []

    def token():
        refreshes.append(1)
        return f"synthetic-token-{len(refreshes)}"

    async def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "response",
                "created_at": 1,
                "model": "deployment",
                **_responses_reply(),
            },
        )

    original = openai.AsyncOpenAI

    def factory(**kwargs):
        return original(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )

    monkeypatch.setattr(openai, "AsyncOpenAI", factory)
    provider = AzureOpenAIResponsesToolCallingProvider(
        model="deployment",
        endpoint="https://resource.openai.azure.com",
        token_provider=token,
        native_async=True,
        client=object(),
    )
    for _ in range(2):
        assert (await provider.anext_action(MESSAGES, TOOLS)).response_text == "ok"
    assert requests[0].headers["authorization"] != requests[1].headers["authorization"]
    assert len(refreshes) == 2
    assert provider.capabilities().supports_native_async
    await provider.aclose()


@pytest.mark.asyncio
async def test_azure_injected_async_client_does_not_require_credentials(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    async def create(**kwargs):
        return _responses_reply()

    provider = AzureOpenAIResponsesToolCallingProvider(
        model="deployment",
        endpoint="https://resource.openai.azure.com",
        async_client=SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    assert (await provider.anext_action(MESSAGES, TOOLS)).metadata[
        "provider"
    ] == "azure_openai"
    with pytest.raises(RuntimeError, match="asynchronous"):
        provider.next_action(MESSAGES, TOOLS)


@pytest.mark.integration
def test_xai_real_sdk_native_tool_and_reasoning_serialization():
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx2")
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            output = [
                {
                    "type": "reasoning",
                    "id": "reason-1",
                    "summary": [],
                    "encrypted_content": "encrypted-native",
                },
                {
                    "type": "function_call",
                    "id": "item-1",
                    "call_id": "xai-call",
                    "name": body["tools"][0]["name"],
                    "arguments": '{"a":2,"b":3}',
                    "status": "completed",
                },
            ]
            reply = {"status": "completed", "output": output}
        else:
            reply = _responses_reply()
        return httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "response",
                "created_at": 1,
                "model": "grok-explicit",
                **reply,
            },
        )

    client = openai.OpenAI(
        api_key="synthetic-key",
        base_url="https://api.x.ai/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    provider = XAIResponsesToolCallingProvider(model="grok-explicit", client=client)
    action = provider.next_action(MESSAGES, TOOLS)
    assert action.metadata["provider"] == "xai"
    assert action.plan.batches[0].calls[0].id == "xai-call"
    history = MESSAGES + [
        json.loads(json.dumps(action.metadata["assistant_tool_message"])),
        {"role": "tool", "tool_call_id": "xai-call", "content": "5"},
    ]
    assert provider.next_action(history, TOOLS).response_text == "ok"
    assert any(
        item.get("encrypted_content") == "encrypted-native"
        for item in requests[1]["input"]
    )
    assert requests[1]["input"][-1] == {
        "type": "function_call_output",
        "call_id": "xai-call",
        "output": "5",
    }
    client.close()


def _bedrock_reply(name="calculator.add", *, calls=True, stop=None):
    content = [
        {
            "reasoningContent": {
                "reasoningText": {
                    "text": "signed reasoning",
                    "signature": "signature-value",
                }
            }
        }
    ]
    if calls:
        content.append(
            {
                "toolUse": {
                    "toolUseId": "call-1",
                    "name": name,
                    "input": {"a": 2, "b": 3},
                }
            }
        )
    else:
        content.append({"text": "5"})
    return {
        "stopReason": stop or ("tool_use" if calls else "end_turn"),
        "output": {"message": {"role": "assistant", "content": content}},
        "usage": {"inputTokens": 3, "outputTokens": 5, "totalTokens": 8},
        "metrics": {"latencyMs": 1},
    }


def test_bedrock_history_and_signed_reasoning():
    requests = []

    def converse(**kwargs):
        requests.append(kwargs)
        wire = (
            kwargs.get("toolConfig", {})
            .get("tools", [{}])[0]
            .get("toolSpec", {})
            .get("name", "")
        )
        return _bedrock_reply(wire, calls=len(requests) == 1)

    provider = BedrockConverseToolCallingProvider(
        model="model-explicit",
        region="us-east-1",
        client=SimpleNamespace(converse=converse),
    )
    action = provider.next_action(MESSAGES, TOOLS)
    call = action.plan.batches[0].calls[0]
    assert (call.id, call.name, call.arguments) == (
        "call-1",
        "calculator.add",
        {"a": 2, "b": 3},
    )
    assert action.metadata["provider"] == "bedrock"
    assert action.metadata["usage"]["total_tokens"] == 8
    assert action.metadata["reasoning"] == "signed reasoning"
    assistant = action.metadata["assistant_tool_message"]
    json.dumps(assistant)
    history = MESSAGES + [
        assistant,
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": {"success": True, "output": 5},
        },
    ]
    final = provider.next_action(history, [])
    assert final.response_text == "5"
    native = requests[1]["messages"][-2]["content"]
    assert (
        native[0]["reasoningContent"]["reasoningText"]["signature"] == "signature-value"
    )
    assert native[1]["toolUse"]["toolUseId"] == "call-1"
    assert (
        requests[1]["messages"][-1]["content"][0]["toolResult"]["toolUseId"] == "call-1"
    )
    assert requests[0]["system"] == [{"text": "Use tools"}]
    assert requests[0]["inferenceConfig"] == {"maxTokens": 1024}


def test_bedrock_redacted_bytes_survive_json_session():
    provider = BedrockConverseToolCallingProvider(
        model="model", region="us-east-1", client=object()
    )
    response = _bedrock_reply(calls=False)
    response["output"]["message"]["content"][0] = {
        "reasoningContent": {"redactedContent": b"\x00\xffnative"}
    }
    action = provider._action(response)
    assistant = json.loads(json.dumps(action.metadata["assistant_message"]))
    request = provider._request(MESSAGES + [assistant], [])
    assert (
        request["messages"][-1]["content"][0]["reasoningContent"]["redactedContent"]
        == b"\x00\xffnative"
    )


@pytest.mark.parametrize(
    "stop",
    [
        "max_tokens",
        "content_filtered",
        "guardrail_intervened",
        "malformed_tool_use",
        "model_context_window_exceeded",
        None,
    ],
)
def test_bedrock_incomplete_rejected(stop):
    provider = BedrockConverseToolCallingProvider(
        model="model", region="us-east-1", client=object()
    )
    reply = _bedrock_reply()
    reply["stopReason"] = stop
    with pytest.raises(ValueError):
        provider._action(reply)


def test_bedrock_duplicate_ids_rejected():
    provider = BedrockConverseToolCallingProvider(
        model="model", region="us-east-1", client=object()
    )
    reply = _bedrock_reply()
    reply["output"]["message"]["content"].append(
        copy.deepcopy(reply["output"]["message"]["content"][-1])
    )
    with pytest.raises(ValueError, match="unique"):
        provider._action(reply)


@pytest.mark.parametrize("dependent", [False, True])
def test_bedrock_parallel_and_sequential_runtime_batches(dependent):
    registry = ToolRegistry()
    registry.register_tool(TOOLS[0], lambda a, b: a + b)
    provider = BedrockConverseToolCallingProvider(
        model="model", region="us-east-1", client=object()
    )
    request = provider._request(MESSAGES, TOOLS)
    wire = request["toolConfig"]["tools"][0]["toolSpec"]["name"]
    reply = _bedrock_reply(wire)
    reply["output"]["message"]["content"].append(
        {
            "toolUse": {
                "toolUseId": "call-2",
                "name": wire,
                "input": {"a": {"$ref": 0} if dependent else 8, "b": 4},
            }
        }
    )
    action = provider._action(reply)
    results = asyncio.run(registry.execute_plan(action.plan))
    assert [result.output for result in results] == [5, 9 if dependent else 12]
    assert all(result.success for result in results)
    assert [batch.mode for batch in action.plan.batches] == (
        ["sequential", "sequential"] if dependent else ["parallel"]
    )


@pytest.mark.integration
def test_bedrock_stream_fixture_matches_native_botocore_event_shape():
    boto3 = pytest.importorskip("boto3")
    from botocore.validate import validate_parameters

    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    shape = client.meta.service_model.operation_model(
        "ConverseStream"
    ).output_shape.members["stream"]
    for event in _tool_stream("add"):
        validate_parameters(event, shape)
    client.close()


class _Stream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __iter__(self):
        yield from self.events

    def close(self):
        self.closed = True


def _tool_stream(name):
    return [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "stream-1", "name": name}},
            }
        },
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"input": '{"a": 2,'}},
            }
        },
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"input": ' "b": 3}'}},
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
        {
            "metadata": {
                "usage": {"inputTokens": 3, "outputTokens": 5, "totalTokens": 8},
                "metrics": {"latencyMs": 1},
            }
        },
    ]


def test_bedrock_stream_only_executes_completed_arguments():
    stream = _Stream([])

    def create(**kwargs):
        stream.events = _tool_stream(
            kwargs["toolConfig"]["tools"][0]["toolSpec"]["name"]
        )
        return {"stream": stream}

    provider = BedrockConverseToolCallingProvider(
        model="model",
        region="us-east-1",
        client=SimpleNamespace(converse_stream=create),
    )
    items = list(provider.stream_next_action(MESSAGES, TOOLS))
    assert len(items) == 1 and isinstance(items[0], AgentAction)
    assert items[0].plan.batches[0].calls[0].arguments == {"a": 2, "b": 3}
    assert items[0].plan.batches[0].calls[0].name == "calculator.add"
    assert stream.closed


def test_bedrock_stream_preserves_signed_and_redacted_reasoning():
    events = [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"reasoningContent": {"text": "think"}},
            }
        },
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"reasoningContent": {"signature": "signature"}},
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {
            "contentBlockDelta": {
                "contentBlockIndex": 1,
                "delta": {"reasoningContent": {"redactedContent": b"opaque"}},
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"contentBlockDelta": {"contentBlockIndex": 2, "delta": {"text": "done"}}},
        {"contentBlockStop": {"contentBlockIndex": 2}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]
    stream = _Stream(events)
    provider = BedrockConverseToolCallingProvider(
        model="model",
        region="us-east-1",
        client=SimpleNamespace(converse_stream=lambda **kwargs: {"stream": stream}),
    )
    items = list(provider.stream_next_action(MESSAGES, []))
    assert items[0] == {"type": "reasoning_chunk", "chunk": "think"}
    assert items[1] == {"type": "text_chunk", "chunk": "done"}
    assistant = json.loads(json.dumps(items[-1].metadata["assistant_message"]))
    native = provider._request(MESSAGES + [assistant], [])["messages"][-1]["content"]
    assert native[0]["reasoningContent"]["reasoningText"]["signature"] == "signature"
    assert native[1]["reasoningContent"]["redactedContent"] == b"opaque"
    assert stream.closed


@pytest.mark.asyncio
async def test_bedrock_async_stream_bridge_retains_native_plan():
    stream = _Stream(_tool_stream("add"))
    provider = BedrockConverseToolCallingProvider(
        model="model",
        region="us-east-1",
        client=SimpleNamespace(converse_stream=lambda **kwargs: {"stream": stream}),
    )
    items = [item async for item in provider.astream_next_action(MESSAGES, TOOLS)]
    assert items[-1].plan.batches[0].calls[0].id == "stream-1"
    assert not provider.capabilities().supports_native_async
    assert stream.closed


@pytest.mark.parametrize("arguments", ["[]", "null", '{"a": NaN}', "unfinished"])
def test_bedrock_stream_malformed_argument_object_rejected(arguments):
    events = _tool_stream("add")
    events[2]["contentBlockDelta"]["delta"]["toolUse"]["input"] = arguments
    events.pop(3)
    stream = _Stream(events)
    provider = BedrockConverseToolCallingProvider(
        model="model",
        region="us-east-1",
        client=SimpleNamespace(converse_stream=lambda **kwargs: {"stream": stream}),
    )
    with pytest.raises(ValueError):
        list(provider.stream_next_action(MESSAGES, TOOLS))
    assert stream.closed


@pytest.mark.parametrize(
    "mutation", ["missing_stop", "truncated", "error", "late_delta", "duplicate_stop"]
)
def test_bedrock_bad_stream_closes_and_never_authorizes(mutation):
    events = _tool_stream("add")
    if mutation == "missing_stop":
        events = events[:-2]
    elif mutation == "truncated":
        events[5]["messageStop"]["stopReason"] = "max_tokens"
    elif mutation == "error":
        events.insert(3, {"modelStreamErrorException": {"message": "failure"}})
    elif mutation == "late_delta":
        events.append(events[2])
    else:
        events.insert(5, events[4])
    stream = _Stream(events)
    provider = BedrockConverseToolCallingProvider(
        model="model",
        region="us-east-1",
        client=SimpleNamespace(converse_stream=lambda **kwargs: {"stream": stream}),
    )
    yielded = []
    with pytest.raises((RuntimeError, ValueError)):
        for item in provider.stream_next_action(MESSAGES, TOOLS):
            yielded.append(item)  # noqa: PERF402 - inspect partial output on failure
    assert not any(isinstance(item, AgentAction) for item in yielded)
    assert stream.closed


@pytest.mark.parametrize(
    "config",
    [
        {"model": ""},
        {"region": "https://example.com"},
        {"max_tokens": True},
        {"temperature": 2},
        {"timeout_seconds": float("inf")},
        {"tool_choice": "required"},
    ],
)
def test_bedrock_invalid_configuration(config):
    with pytest.raises(ValueError):
        BedrockConverseToolCallingProvider(
            **{"model": "model", "region": "us-east-1", "client": object(), **config}
        )


@pytest.mark.integration
def test_bedrock_real_sdk_serializes_converse_without_network():
    boto3 = pytest.importorskip("boto3")
    from botocore.stub import Stubber

    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    provider = BedrockConverseToolCallingProvider(
        model="model-explicit", region="us-east-1", client=client
    )
    expected = provider._request(MESSAGES, TOOLS)
    reply = _bedrock_reply(expected["toolConfig"]["tools"][0]["toolSpec"]["name"])
    with Stubber(client) as stub:
        stub.add_response("converse", reply, expected)
        assert (
            provider.next_action(MESSAGES, TOOLS).plan.batches[0].calls[0].id
            == "call-1"
        )
        stub.assert_no_pending_responses()
    client.close()


def _gemini_reply():
    part = SimpleNamespace(
        function_call=SimpleNamespace(
            id="vertex-call", name="calculator.add", args={"a": 2, "b": 3}
        ),
        text=None,
        thought_signature=b"vertex-signature",
    )
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(finish_reason="STOP", content=SimpleNamespace(parts=[part]))
        ],
        text="",
        usage_metadata=SimpleNamespace(prompt_token_count=2, candidates_token_count=3),
    )


def test_vertex_uses_native_gemini_thought_signature_replay():
    requests = []

    def generate(**kwargs):
        requests.append(kwargs)
        return _gemini_reply()

    provider = VertexGeminiToolCallingProvider(
        model="gemini-explicit",
        project="project-id",
        location="global",
        client=SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
    )
    action = provider.next_action(MESSAGES, TOOLS)
    assert action.metadata["provider"] == "vertex"
    assert action.plan.metadata["provider"] == "vertex"
    assistant = json.loads(json.dumps(action.metadata["assistant_tool_message"]))
    provider.next_action(
        MESSAGES
        + [
            assistant,
            {
                "role": "tool",
                "tool_call_id": "vertex-call",
                "name": "calculator.add",
                "content": {"output": 5},
            },
        ],
        TOOLS,
    )
    parts = requests[1]["contents"][-2].parts
    assert parts[0].thought_signature == b"vertex-signature"


@pytest.mark.asyncio
async def test_vertex_native_async_uses_aio_and_keeps_client_immutable():
    requests = []

    async def generate(**kwargs):
        requests.append(kwargs)
        return _gemini_reply()

    def forbidden(**kwargs):
        raise AssertionError("native async must not call sync SDK")

    client = SimpleNamespace(
        models=SimpleNamespace(generate_content=forbidden),
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
    )
    provider = VertexGeminiToolCallingProvider(
        model="gemini-explicit",
        project="project-id",
        location="global",
        client=client,
        native_async=True,
    )
    action = await provider.anext_action(MESSAGES, TOOLS)
    assert action.plan.batches[0].calls[0].id == "vertex-call"
    assert action.metadata["provider"] == "vertex"
    assert provider.capabilities().supports_native_async
    assert provider._client is client
    assert len(requests) == 1


@pytest.mark.integration
def test_vertex_real_sdk_client_configuration_and_schema(monkeypatch):
    genai = pytest.importorskip("google.genai")
    constructor_calls = []
    credentials = object()

    def construct(**kwargs):
        constructor_calls.append(kwargs)
        return SimpleNamespace(
            models=SimpleNamespace(generate_content=lambda **kwargs: _gemini_reply())
        )

    monkeypatch.setattr(genai, "Client", construct)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-developer-key-not-used")
    provider = VertexGeminiToolCallingProvider(
        model="gemini-explicit",
        project="project-id",
        location="global",
        credentials=credentials,
    )
    assert constructor_calls == [
        {
            "vertexai": True,
            "project": "project-id",
            "location": "global",
            "credentials": credentials,
        }
    ]
    request = provider._request(MESSAGES, TOOLS)
    config = genai.types.GenerateContentConfig.model_validate(request["config"])
    assert config.max_output_tokens == 1024
    assert config.tools[0].function_declarations[0].name == "calculator.add"


@pytest.mark.parametrize(
    "config",
    [
        {"model": ""},
        {"project": "bad/project"},
        {"location": "https://remote"},
        {"native_async": "yes"},
    ],
)
def test_vertex_invalid_configuration(config):
    with pytest.raises((ValueError, TypeError)):
        VertexGeminiToolCallingProvider(
            **{
                "model": "gemini-explicit",
                "project": "project-id",
                "location": "global",
                "client": object(),
                **config,
            }
        )


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "SAFETY", "MALFORMED_FUNCTION_CALL"])
def test_vertex_incomplete_tool_candidate_rejected(finish):
    response = _gemini_reply()
    response.candidates[0].finish_reason = finish
    provider = VertexGeminiToolCallingProvider(
        model="gemini-explicit",
        project="project-id",
        location="global",
        client=SimpleNamespace(
            models=SimpleNamespace(generate_content=lambda **kwargs: response)
        ),
    )
    with pytest.raises(ValueError, match="did not complete"):
        provider.next_action(MESSAGES, TOOLS)
