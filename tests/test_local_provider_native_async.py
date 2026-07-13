from types import SimpleNamespace

import pytest

from mtp.protocol import ToolSpec
from mtp.providers.lmstudio_provider import LMStudioToolCallingProvider
from mtp.providers.ollama_provider import OllamaToolCallingProvider


class AsyncStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.chunks)
        except StopIteration:
            raise StopAsyncIteration


class AsyncOllama:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def chat(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


class AsyncCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


TOOL = ToolSpec("weather", "weather", {"type": "object"})


@pytest.mark.asyncio
async def test_ollama_native_async_action_preserves_options_thinking_and_usage():
    response = {
        "message": {
            "content": "checking",
            "thinking": "reason",
            "tool_calls": [{"function": {"name": "weather", "arguments": {"city": "Pune"}}}],
        },
        "prompt_eval_count": 4,
        "eval_count": 3,
    }
    client = AsyncOllama([response])
    provider = OllamaToolCallingProvider(
        client=object(), async_client=client, model="qwen-test", options={"num_ctx": 4096},
        format="json", keep_alive="5m", think="high",
    )

    action = await provider.anext_action([{"role": "user", "content": "go"}], [TOOL])

    assert action.plan.batches[0].calls[0].arguments == {"city": "Pune"}
    assert action.metadata["reasoning"] == "reason"
    assert action.metadata["usage"]["total_tokens"] == 7
    assert client.requests[0] == {
        "messages": [{"role": "user", "content": "go"}], "model": "qwen-test", "stream": False,
        "options": {"num_ctx": 4096}, "format": "json", "keep_alive": "5m", "think": "high",
        "tools": [{"type": "function", "function": {"name": "weather", "description": "weather", "parameters": {"type": "object"}}}],
    }
    assert provider.capabilities().supports_native_async


@pytest.mark.asyncio
async def test_ollama_native_async_finalize_stream_accumulates_thinking_and_usage():
    client = AsyncOllama([AsyncStream([
        {"message": {"thinking": "one ", "content": "Hi"}},
        {"message": {"thinking": "two", "content": "!"}, "prompt_eval_count": 2, "eval_count": 1},
    ])])
    provider = OllamaToolCallingProvider(client=object(), async_client=client, think=True)
    chunks = [chunk async for chunk in provider.afinalize_stream([], [])]
    assert chunks == ["Hi", "!"]
    assert provider._last_stream_thinking == "one two"
    assert provider._last_stream_usage["total_tokens"] == 3


def lm_response(content="", tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content="think"))],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7),
    )


@pytest.mark.asyncio
async def test_lmstudio_native_async_action_preserves_tools_options_reasoning_usage():
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="weather", arguments='{"city":"Pune"}'))
    completions = AsyncCompletions([lm_response("checking", [call])])
    provider = LMStudioToolCallingProvider(
        client=object(), async_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="local-test", temperature=0.2, tool_choice="required", parallel_tool_calls=False,
    )
    action = await provider.anext_action([{"role": "user", "content": "go"}], [TOOL])
    assert action.plan.batches[0].calls[0].arguments == {"city": "Pune"}
    assert action.metadata["reasoning"] == "think"
    assert action.metadata["usage"]["total_tokens"] == 7
    request = completions.requests[0]
    assert request["temperature"] == 0.2
    assert request["tool_choice"] == "required"
    assert request["parallel_tool_calls"] is False


@pytest.mark.asyncio
async def test_lmstudio_native_async_planning_stream_merges_fragments_and_usage():
    fragment1 = SimpleNamespace(index=0, id="c1", function=SimpleNamespace(name="wea", arguments='{"city":'))
    fragment2 = SimpleNamespace(index=0, id=None, function=SimpleNamespace(name="ther", arguments='"Pune"}'))
    stream = AsyncStream([
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content="why ", tool_calls=[fragment1]))], usage=None),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", reasoning_content="now", tool_calls=[fragment2]))], usage=None),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)),
    ])
    completions = AsyncCompletions([stream])
    provider = LMStudioToolCallingProvider(client=object(), async_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    events = [event async for event in provider.astream_next_action([], [TOOL])]
    action = events[-1]
    assert action.plan.batches[0].calls[0].name == "weather"
    assert action.plan.batches[0].calls[0].arguments == {"city": "Pune"}
    assert action.metadata["reasoning"] == "why now"
    assert action.metadata["usage"]["total_tokens"] == 5
    assert completions.requests[0]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_lmstudio_native_async_finalize_stream_records_usage():
    stream = AsyncStream([
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"))], usage=None),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3)),
    ])
    completions = AsyncCompletions([stream])
    provider = LMStudioToolCallingProvider(client=object(), async_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    assert [part async for part in provider.afinalize_stream([], [])] == ["hello"]
    assert provider._last_stream_usage["total_tokens"] == 3
    assert completions.requests[0]["stream_options"] == {"include_usage": True}
