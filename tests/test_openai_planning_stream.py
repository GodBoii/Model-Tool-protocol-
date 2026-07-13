from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.agent import AgentAction
from mtp.protocol import ToolSpec
from mtp.providers.openai_provider import OpenAIToolCallingProvider


def _chunks() -> list[object]:
    # Calls are deliberately interleaved and mix decoded dictionaries with SDK
    # model-like objects. Every string field is fragmented, including JSON.
    return [
        {"choices": [{"delta": {"content": "I will ", "reasoning_content": "think ", "tool_calls": [
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
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
            "completion_tokens_details": {"reasoning_tokens": 3},
        }},
    ]


class _SyncCompletions:
    def __init__(self) -> None:
        self.request: dict[str, object] = {}

    def create(self, **kwargs):
        self.request = kwargs
        return iter(_chunks())


class _AsyncChunks:
    def __init__(self) -> None:
        self._items = iter(_chunks())

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


class _AsyncCompletions:
    def __init__(self) -> None:
        self.request: dict[str, object] = {}

    async def create(self, **kwargs):
        self.request = kwargs
        return _AsyncChunks()


def _tools() -> list[ToolSpec]:
    return [
        ToolSpec(name="weather.get", description="Weather"),
        ToolSpec(name="math.double", description="Double"),
    ]


def _assert_result(items: list[AgentAction | dict[str, object]]) -> None:
    assert items[:-1] == [
        {"type": "text_chunk", "chunk": "I will "},
        {"type": "text_chunk", "chunk": "check"},
    ]
    action = items[-1]
    assert isinstance(action, AgentAction)
    assert action.plan is not None
    calls = [call for batch in action.plan.batches for call in batch.calls]
    assert [(call.id, call.name, call.arguments) for call in calls] == [
        ("call_weather", "weather.get", {"city": "Pune"}),
        ("call_math", "math.double", {"x": 2}),
    ]
    assert "reasoning" not in action.metadata
    assert action.metadata["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "reasoning_tokens": 3,
    }
    assistant = action.metadata["assistant_tool_message"]
    assert assistant["content"] == "I will check"
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"city":"Pune"}'


def test_openai_stream_next_action_accumulates_fragmented_parallel_calls() -> None:
    completions = _SyncCompletions()
    provider = OpenAIToolCallingProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )

    items = list(provider.stream_next_action([{"role": "user", "content": "go"}], _tools()))

    _assert_result(items)
    assert provider._last_stream_usage == action_usage(items)
    assert completions.request["stream"] is True
    assert completions.request["stream_options"] == {"include_usage": True}
    assert completions.request["parallel_tool_calls"] is True


@pytest.mark.asyncio
async def test_openai_astream_next_action_accumulates_fragmented_parallel_calls() -> None:
    completions = _AsyncCompletions()
    provider = OpenAIToolCallingProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=object())),
        async_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    items = [
        item
        async for item in provider.astream_next_action(
            [{"role": "user", "content": "go"}], _tools()
        )
    ]

    _assert_result(items)
    assert provider._last_stream_usage == action_usage(items)
    assert completions.request["stream"] is True
    assert completions.request["stream_options"] == {"include_usage": True}


def action_usage(items: list[AgentAction | dict[str, object]]) -> dict[str, int]:
    action = items[-1]
    assert isinstance(action, AgentAction)
    return action.metadata["usage"]
