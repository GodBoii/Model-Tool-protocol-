from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.agent import Agent, AgentAction
from mtp.protocol import ToolSpec
from mtp.providers.openai_provider import OpenAIToolCallingProvider
from mtp.runtime import ToolRegistry


def _response(
    content: str = "done",
    *,
    tool_calls: list[object] | None = None,
    prompt_tokens: int = 3,
    completion_tokens: int = 2,
) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


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
        self.with_raw_response = None

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


class _FailingSyncCompletions:
    with_raw_response = None

    def create(self, **kwargs):
        raise AssertionError("native async path called the synchronous client")


def _provider(async_completions: _AsyncCompletions, **kwargs) -> OpenAIToolCallingProvider:
    sync_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FailingSyncCompletions())
    )
    async_client = SimpleNamespace(
        chat=SimpleNamespace(completions=async_completions)
    )
    return OpenAIToolCallingProvider(
        client=sync_client,
        async_client=async_client,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_openai_anext_action_is_native_and_preserves_options_usage() -> None:
    function = SimpleNamespace(name="weather.get", arguments='{"city":"Pune"}')
    tool_call = SimpleNamespace(id="call_1", function=function)
    completions = _AsyncCompletions([_response("checking", tool_calls=[tool_call])])
    response_format = {"type": "json_object"}
    provider = _provider(
        completions,
        model="gpt-test",
        strict_tools=True,
        response_format=response_format,
        max_completion_tokens=41,
        timeout=2.5,
    )
    tool = ToolSpec(
        name="weather.get",
        description="Get weather",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
    )

    action = await provider.anext_action([{"role": "user", "content": "weather"}], [tool])

    assert action.plan is not None
    assert action.plan.batches[0].calls[0].arguments == {"city": "Pune"}
    assert action.metadata["usage"] == {
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
    }
    request = completions.requests[0]
    assert request["model"] == "gpt-test"
    assert request["tools"][0]["function"]["strict"] is True
    assert request["response_format"] == response_format
    assert request["max_completion_tokens"] == 41
    assert request["timeout"] == 2.5
    assert provider.capabilities().supports_native_async is True


@pytest.mark.asyncio
async def test_openai_async_raw_response_preserves_rate_limits_and_finalize_usage() -> None:
    class RawResponse:
        headers = {
            "x-ratelimit-remaining-requests": "19",
            "content-type": "application/json",
        }

        async def parse(self):
            return _response("final", prompt_tokens=8, completion_tokens=4)

    class RawCompletions(_AsyncCompletions):
        def __init__(self) -> None:
            super().__init__([])
            self.with_raw_response = self

        async def create(self, **kwargs):
            self.requests.append(kwargs)
            return RawResponse()

    completions = RawCompletions()
    provider = _provider(completions)

    text = await provider.afinalize([{"role": "user", "content": "answer"}], [])

    assert text == "final"
    assert provider._last_finalize_usage == {
        "input_tokens": 8,
        "output_tokens": 4,
        "total_tokens": 12,
    }
    assert provider._last_finalize_rate_limits == {
        "x-ratelimit-remaining-requests": "19"
    }


@pytest.mark.asyncio
async def test_openai_afinalize_stream_is_native_and_remembers_usage() -> None:
    stream = _AsyncStream(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))],
                usage=None,
            ),
            {"choices": [{"delta": {"content": " async"}}]},
            SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=6, completion_tokens=2, total_tokens=8
                ),
            ),
        ]
    )
    completions = _AsyncCompletions([stream])
    provider = _provider(completions, max_completion_tokens=77, timeout=4.0)

    chunks = [
        chunk
        async for chunk in provider.afinalize_stream(
            [{"role": "user", "content": "hello"}], []
        )
    ]

    assert chunks == ["Hello", " async"]
    assert provider._last_stream_usage == {
        "input_tokens": 6,
        "output_tokens": 2,
        "total_tokens": 8,
    }
    request = completions.requests[0]
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert request["max_completion_tokens"] == 77
    assert request["timeout"] == 4.0


@pytest.mark.asyncio
async def test_agent_async_events_prefer_native_async_finalize_stream() -> None:
    class Provider(OpenAIToolCallingProvider):
        async def anext_action(self, messages, tools):
            return AgentAction(plan=None)

        def finalize_stream(self, messages, tool_results):
            raise AssertionError("async event loop used synchronous streaming")

    stream = _AsyncStream(
        [
            {"choices": [{"delta": {"content": "nonblocking"}}]},
            {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        ]
    )
    provider = _provider(_AsyncCompletions([stream]))
    provider.__class__ = Provider
    agent = Agent(provider=provider, tools=ToolRegistry())

    events = [event async for event in agent.arun_loop_events("go", max_rounds=1)]

    chunks = [event["chunk"] for event in events if event["type"] == "text_chunk"]
    assert chunks == ["nonblocking"]
    assert events[-1]["type"] == "run_completed"
    assert events[-1]["final_text"] == "nonblocking"
