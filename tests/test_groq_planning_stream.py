from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.agent import AgentAction
from mtp.provider_errors import ProviderError, ProviderErrorCategory
from mtp.protocol import ToolSpec
from mtp.providers.groq_provider import GroqToolCallingProvider


def _chunks() -> list[object]:
    return [
        {"choices": [{"delta": {"content": "I will ", "reasoning": "think ", "tool_calls": [
            {"index": 1, "id": "call_", "function": {"name": "math.", "arguments": "{\"x\":"}}
        ]}}]},
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            reasoning_content="now", tool_calls=[SimpleNamespace(
                index=0, id="call_", function=SimpleNamespace(name="weather.", arguments="{\"city\":\"")
            )]
        ))], usage=None),
        {"choices": [{"delta": {"content": "check", "tool_calls": [
            {"index": 0, "id": "weather", "function": {"name": "get", "arguments": "Pune\"}"}},
            {"index": 1, "id": "math", "function": {"name": "double", "arguments": "2}"}},
        ]}}]},
        {"choices": [], "usage": {
            "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
            "completion_tokens_details": {"reasoning_tokens": 3},
        }},
    ]


class _SyncCompletions:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return iter(_chunks())


class _AsyncChunks:
    def __init__(self) -> None:
        self.items = iter(_chunks())

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.items)
        except StopIteration:
            raise StopAsyncIteration


class _AsyncCompletions:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return _AsyncChunks()


def _tools() -> list[ToolSpec]:
    return [ToolSpec(name="weather.get", description="Weather"), ToolSpec(name="math.double", description="Double")]


def _assert_result(items: list[AgentAction | dict[str, object]], provider: GroqToolCallingProvider) -> None:
    assert items[:-1] == [
        {"type": "reasoning_chunk", "chunk": "think "},
        {"type": "text_chunk", "chunk": "I will "},
        {"type": "reasoning_chunk", "chunk": "now"},
        {"type": "text_chunk", "chunk": "check"},
    ]
    action = items[-1]
    assert isinstance(action, AgentAction) and action.plan is not None
    calls = [call for batch in action.plan.batches for call in batch.calls]
    assert [(call.id, call.name, call.arguments) for call in calls] == [
        ("call_weather", "weather.get", {"city": "Pune"}),
        ("call_math", "math.double", {"x": 2}),
    ]
    assert action.metadata["reasoning"] == "think now"
    assert action.metadata["usage"] == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18, "reasoning_tokens": 3}
    assert action.metadata["tool_call_source"] == "streamed_native_tool_calls"
    assert provider._last_stream_usage == action.metadata["usage"]


def test_groq_streamed_planning_accumulates_fragmented_parallel_calls() -> None:
    completions = _SyncCompletions()
    provider = GroqToolCallingProvider(client=SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    provider.include_reasoning = True
    provider.reasoning_format = "hidden"
    provider.reasoning_effort = "default"
    items = list(provider.stream_next_action([{"role": "user", "content": "go"}], _tools()))
    _assert_result(items, provider)
    request = completions.requests[0]
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert request["parallel_tool_calls"] is True
    assert request["include_reasoning"] is True
    assert request["reasoning_format"] == "hidden"
    assert request["reasoning_effort"] == "default"


@pytest.mark.asyncio
async def test_groq_native_async_streamed_planning_accumulates_fragments() -> None:
    completions = _AsyncCompletions()
    provider = GroqToolCallingProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=object())),
        async_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    items = [item async for item in provider.astream_next_action([{"role": "user", "content": "go"}], _tools())]
    _assert_result(items, provider)
    assert completions.requests[0]["stream"] is True


def test_groq_stream_iteration_errors_are_normalized() -> None:
    class RateLimitError(Exception):
        status_code = 429

    class FailingCompletions:
        def create(self, **kwargs):
            def chunks():
                yield {"choices": [{"delta": {"content": "partial"}}]}
                raise RateLimitError("secret response body")
            return chunks()

    provider = GroqToolCallingProvider(client=SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions())))
    with pytest.raises(ProviderError) as caught:
        list(provider.stream_next_action([{"role": "user", "content": "go"}], []))
    assert caught.value.category is ProviderErrorCategory.RATE_LIMIT
    assert "secret response body" not in str(caught.value)
