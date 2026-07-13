from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mtp.protocol import ToolSpec
from mtp.providers.fireworks_provider import FireworksAIToolCallingProvider
from mtp.providers.openrouter_provider import OpenRouterToolCallingProvider
from mtp.providers.together_provider import TogetherAIToolCallingProvider


def _response(
    content: str = "done", *, tool_calls: list[object] | None = None
) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=7, completion_tokens=3, total_tokens=10
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
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.requests.append(kwargs)
        return self.responses.pop(0)


class _FailingSyncCompletions:
    def create(self, **kwargs: Any) -> object:
        raise AssertionError("native async path called the synchronous client")


def _provider(
    provider_type: type,
    completions: _AsyncCompletions,
    **kwargs: Any,
) -> Any:
    return provider_type(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=_FailingSyncCompletions())
        ),
        async_client=SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        ),
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_type", "provider_name", "extra", "expected"),
    [
        (
            OpenRouterToolCallingProvider,
            "openrouter",
            {"response_format": {"type": "json_object"}},
            {"response_format": {"type": "json_object"}},
        ),
        (
            TogetherAIToolCallingProvider,
            "together",
            {"max_tokens": 123},
            {"max_tokens": 123},
        ),
        (
            FireworksAIToolCallingProvider,
            "fireworks",
            {
                "max_tokens": 234,
                "response_format": {"type": "json_schema", "json_schema": {}},
            },
            {
                "max_tokens": 234,
                "response_format": {"type": "json_schema", "json_schema": {}},
            },
        ),
    ],
)
async def test_compatible_anext_action_is_native_and_preserves_request_options(
    provider_type: type,
    provider_name: str,
    extra: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    function = SimpleNamespace(name="weather.get", arguments='{"city":"Pune"}')
    tool_call = SimpleNamespace(id="call_1", function=function)
    completions = _AsyncCompletions(
        [_response("checking", tool_calls=[tool_call])]
    )
    provider = _provider(
        provider_type,
        completions,
        model="test-model",
        temperature=0.25,
        tool_choice="required",
        parallel_tool_calls=False,
        **extra,
    )
    tool = ToolSpec(
        name="weather.get",
        description="Get weather",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
        },
    )

    action = await provider.anext_action(
        [{"role": "user", "content": "weather"}], [tool]
    )

    assert action.plan is not None
    assert action.plan.batches[0].calls[0].arguments == {"city": "Pune"}
    assert action.metadata["provider"] == provider_name
    assert action.metadata["usage"] == {
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 10,
    }
    request = completions.requests[0]
    assert request["model"] == "test-model"
    assert request["temperature"] == 0.25
    assert request["tool_choice"] == "required"
    assert request["parallel_tool_calls"] is False
    assert request["tools"][0]["function"]["name"] == "weather.get"
    for key, value in expected.items():
        assert request[key] == value
    assert provider.capabilities().supports_native_async is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_type", "extra", "expects_stream_options"),
    [
        (OpenRouterToolCallingProvider, {}, True),
        (TogetherAIToolCallingProvider, {"max_tokens": 321}, True),
        (
            FireworksAIToolCallingProvider,
            {
                "max_tokens": 432,
                "response_format": {"type": "json_object"},
            },
            True,
        ),
    ],
)
async def test_compatible_afinalize_and_stream_are_native_and_record_usage(
    provider_type: type,
    extra: dict[str, Any],
    expects_stream_options: bool,
) -> None:
    stream = _AsyncStream(
        [
            {"choices": [{"delta": {"content": "Hello"}}]},
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=" async"))],
                usage=None,
            ),
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            },
        ]
    )
    completions = _AsyncCompletions([_response("final"), stream])
    provider = _provider(provider_type, completions, **extra)

    text = await provider.afinalize(
        [{"role": "user", "content": "answer"}], []
    )
    chunks = [
        chunk
        async for chunk in provider.afinalize_stream(
            [{"role": "user", "content": "stream"}], []
        )
    ]

    assert text == "final"
    assert provider._last_finalize_usage == {
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 10,
    }
    assert chunks == ["Hello", " async"]
    assert provider._last_stream_usage == {
        "input_tokens": 5,
        "output_tokens": 2,
        "total_tokens": 7,
    }
    request = completions.requests[1]
    assert request["stream"] is True
    if expects_stream_options:
        assert request["stream_options"] == {"include_usage": True}
    else:
        assert "stream_options" not in request


@pytest.mark.asyncio
async def test_together_async_preserves_parallel_tool_compatibility_retry() -> None:
    class RetryCompletions(_AsyncCompletions):
        async def create(self, **kwargs: Any) -> object:
            self.requests.append(kwargs)
            if "parallel_tool_calls" in kwargs:
                    raise TypeError("unexpected keyword argument 'parallel_tool_calls'")
            return self.responses.pop(0)

    completions = RetryCompletions([_response("fallback")])
    provider = _provider(TogetherAIToolCallingProvider, completions)
    tool = ToolSpec(name="echo", description="Echo", input_schema={"type": "object"})

    action = await provider.anext_action([], [tool])

    assert action.response_text == "fallback"
    assert "parallel_tool_calls" in completions.requests[0]
    assert "parallel_tool_calls" not in completions.requests[1]


@pytest.mark.asyncio
async def test_together_async_does_not_retry_service_failures() -> None:
    class FailingCompletions(_AsyncCompletions):
        async def create(self, **kwargs: Any) -> object:
            self.requests.append(kwargs)
            raise RuntimeError("rate limited")

    completions = FailingCompletions([])
    provider = _provider(TogetherAIToolCallingProvider, completions)
    tool = ToolSpec(name="echo", description="Echo", input_schema={"type": "object"})

    with pytest.raises(RuntimeError, match="rate limited"):
        await provider.anext_action([], [tool])
    assert len(completions.requests) == 1


@pytest.mark.asyncio
async def test_fireworks_async_only_retries_parallel_option_type_errors() -> None:
    class RetryCompletions(_AsyncCompletions):
        async def create(self, **kwargs: Any) -> object:
            self.requests.append(kwargs)
            if "parallel_tool_calls" in kwargs:
                raise TypeError("unsupported option")
            return self.responses.pop(0)

    completions = RetryCompletions([_response("fallback")])
    provider = _provider(FireworksAIToolCallingProvider, completions)
    tool = ToolSpec(name="echo", description="Echo", input_schema={"type": "object"})

    action = await provider.anext_action([], [tool])

    assert action.response_text == "fallback"
    assert "parallel_tool_calls" in completions.requests[0]
    assert "parallel_tool_calls" not in completions.requests[1]
