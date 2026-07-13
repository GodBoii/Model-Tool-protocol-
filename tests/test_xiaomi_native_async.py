from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.agent import AgentAction
from mtp.protocol import ToolSpec
from mtp.providers.xiaomi_provider import XiaomiToolCallingProvider


class _AsyncStream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


class _AsyncCompletions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.requests.append(dict(kwargs))
        return self.responses.pop(0)


class _FailingSyncCompletions:
    def create(self, **kwargs):
        raise AssertionError("native Xiaomi async operation used the sync client")


def _provider(completions: _AsyncCompletions, **kwargs) -> XiaomiToolCallingProvider:
    return XiaomiToolCallingProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=_FailingSyncCompletions())
        ),
        async_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        **kwargs,
    )


def _response(content="done", *, calls=None, reasoning="thinking", usage=(3, 2)):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=calls,
                    reasoning_content=reasoning,
                )
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=usage[0],
            completion_tokens=usage[1],
            total_tokens=sum(usage),
        ),
    )


@pytest.mark.asyncio
async def test_xiaomi_anext_action_is_native_and_preserves_options() -> None:
    call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="weather.get", arguments='{"city":"Pune"}'),
    )
    completions = _AsyncCompletions([_response("checking", calls=[call])])
    provider = _provider(
        completions,
        model="mimo-test",
        temperature=0.25,
        tool_choice="required",
        parallel_tool_calls=True,
        thinking_mode="enabled",
    )
    tool = ToolSpec(name="weather.get", description="Get weather")

    action = await provider.anext_action([{"role": "user", "content": "weather"}], [tool])

    assert action.plan is not None
    assert action.plan.batches[0].calls[0].arguments == {"city": "Pune"}
    assert action.metadata["reasoning"] == "thinking"
    assert action.metadata["usage"] == {
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
    }
    request = completions.requests[0]
    assert request["model"] == "mimo-test"
    assert request["temperature"] == 0.25
    assert request["tool_choice"] == "required"
    assert request["parallel_tool_calls"] is True
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert provider.capabilities().supports_native_async is True


@pytest.mark.asyncio
async def test_xiaomi_astream_next_action_assembles_fragmented_parallel_calls() -> None:
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(
                content="I will ",
                reasoning_content="think ",
                tool_calls=[SimpleNamespace(
                    index=1,
                    id="call_math",
                    function=SimpleNamespace(name="math.", arguments='{"x":'),
                )],
            ))],
            usage=None,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(
                content="check",
                reasoning_content="now",
                tool_calls=[
                    SimpleNamespace(
                        index=0,
                        id="call_weather",
                        function=SimpleNamespace(
                            name="weather.get", arguments='{"city":"Pune"}'
                        ),
                    ),
                    SimpleNamespace(
                        index=1,
                        id=None,
                        function=SimpleNamespace(name="double", arguments="2}"),
                    ),
                ],
            ))],
            usage=None,
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=11, completion_tokens=7, total_tokens=18
            ),
        ),
    ]
    completions = _AsyncCompletions([_AsyncStream(chunks)])
    provider = _provider(completions)

    items = [
        item
        async for item in provider.astream_next_action(
            [{"role": "user", "content": "go"}],
            [
                ToolSpec(name="weather.get", description="Weather"),
                ToolSpec(name="math.double", description="Double"),
            ],
        )
    ]

    assert items[:-1] == [
        {"type": "reasoning_chunk", "chunk": "think "},
        {"type": "text_chunk", "chunk": "I will "},
        {"type": "reasoning_chunk", "chunk": "now"},
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
    assert action.metadata["reasoning"] == "think now"
    assert action.metadata["usage"]["total_tokens"] == 18
    request = completions.requests[0]
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_xiaomi_afinalize_and_stream_are_native_and_remember_metadata() -> None:
    final_response = _response("final answer", reasoning="carefully", usage=(8, 4))
    final_stream = _AsyncStream(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(
                    content="Hello", reasoning_content="reason "
                ))],
                usage=None,
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(
                    content=" async", reasoning_content="more"
                ))],
                usage=None,
            ),
            SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=6, completion_tokens=2, total_tokens=8
                ),
            ),
        ]
    )
    completions = _AsyncCompletions([final_response, final_stream])
    provider = _provider(completions, final_thinking_mode="adaptive")

    text = await provider.afinalize([{"role": "user", "content": "answer"}], [])
    streamed = [
        chunk
        async for chunk in provider.afinalize_stream(
            [{"role": "user", "content": "answer"}], []
        )
    ]

    assert text == "final answer"
    assert provider._last_finalize_usage == {
        "input_tokens": 8,
        "output_tokens": 4,
        "total_tokens": 12,
    }
    assert streamed == ["Hello", " async"]
    assert provider._last_stream_usage == {
        "input_tokens": 6,
        "output_tokens": 2,
        "total_tokens": 8,
    }
    assert provider._last_stream_reasoning == "reason more"
    assert provider._last_finalize_message == {
        "role": "assistant",
        "content": "Hello async",
        "reasoning": "reason more",
        "reasoning_content": "reason more",
    }
    assert completions.requests[0]["extra_body"] == {
        "thinking": {"type": "adaptive"}
    }
    assert completions.requests[1]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_xiaomi_async_parallel_option_fallback_is_non_mutating() -> None:
    class Completions(_AsyncCompletions):
        async def create(self, **kwargs):
            self.requests.append(dict(kwargs))
            if "parallel_tool_calls" in kwargs:
                raise TypeError("unsupported")
            return _response()

    completions = Completions([])
    provider = _provider(completions)
    args = provider._request_args(
        [{"role": "user", "content": "go"}],
        [ToolSpec(name="demo", description="Demo")],
    )

    await provider._acreate_completion(args)

    assert "parallel_tool_calls" in args
    assert "parallel_tool_calls" in completions.requests[0]
    assert "parallel_tool_calls" not in completions.requests[1]
