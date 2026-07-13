from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.protocol import ToolSpec
from mtp.providers.anthropic_provider import AnthropicToolCallingProvider


def _message(
    content: list[object],
    *,
    input_tokens: int = 5,
    output_tokens: int = 3,
    stop_reason: str = "end_turn",
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=2,
            cache_read_input_tokens=4,
        ),
    )


class _FailingSyncMessages:
    def create(self, **kwargs):
        raise AssertionError("native async path called the synchronous client")

    def stream(self, **kwargs):
        raise AssertionError("native async path called the synchronous stream")


class _AsyncTextStream:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


class _AsyncMessageStream:
    def __init__(self, final_message: object) -> None:
        self.text_stream = _AsyncTextStream(["Hello", "", " async Claude"])
        self.final_message = final_message
        self.entered = False
        self.exited = False
        self.final_message_requested = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.exited = True

    async def get_final_message(self):
        self.final_message_requested = True
        return self.final_message


class _AsyncMessages:
    def __init__(self, responses: list[object], stream: _AsyncMessageStream | None = None) -> None:
        self.responses = list(responses)
        self.message_stream = stream
        self.create_requests: list[dict[str, object]] = []
        self.stream_requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.create_requests.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def stream(self, **kwargs):
        self.stream_requests.append(kwargs)
        assert self.message_stream is not None
        return self.message_stream


def _provider(messages: _AsyncMessages, **kwargs) -> AnthropicToolCallingProvider:
    return AnthropicToolCallingProvider(
        client=SimpleNamespace(messages=_FailingSyncMessages()),
        async_client=SimpleNamespace(messages=messages),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_anthropic_anext_action_is_native_and_preserves_tools_usage_metadata() -> None:
    response = _message(
        [
            SimpleNamespace(type="text", text="I will check."),
            SimpleNamespace(
                type="tool_use",
                id="toolu_1",
                name="weather.get",
                input={"city": "Pune"},
            ),
        ]
    )
    messages_api = _AsyncMessages([response])
    provider = _provider(
        messages_api,
        model="claude-test",
        max_tokens=456,
        temperature=0.2,
    )
    tool = ToolSpec(
        name="weather.get",
        description="Get weather",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
    )

    action = await provider.anext_action(
        [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Weather?"},
        ],
        [tool],
    )

    assert action.plan is not None
    assert action.plan.batches[0].calls[0].arguments == {"city": "Pune"}
    assert action.metadata["usage"] == {
        "input_tokens": 5,
        "output_tokens": 3,
        "total_tokens": 8,
        "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 4,
    }
    assert action.metadata["assistant_tool_message"]["content"] == "I will check."
    assert messages_api.create_requests == [
        {
            "model": "claude-test",
            "max_tokens": 456,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Weather?"}]}
            ],
            "tools": [
                {
                    "name": "weather.get",
                    "description": "Get weather",
                    "input_schema": tool.input_schema,
                }
            ],
            "temperature": 0.2,
            "system": "Be concise.",
        }
    ]
    assert provider.capabilities().supports_native_async is True


@pytest.mark.asyncio
async def test_anthropic_afinalize_is_native_and_remembers_terminal_metadata() -> None:
    response = _message(
        [SimpleNamespace(type="text", text="Final answer")],
        input_tokens=12,
        output_tokens=2,
        stop_reason="stop_sequence",
    )
    response.stop_sequence = "END"
    messages_api = _AsyncMessages([response])
    provider = _provider(messages_api, model="claude-test", max_tokens=99)

    text = await provider.afinalize([{"role": "user", "content": "Answer"}], [])

    assert text == "Final answer"
    assert provider._last_finalize_usage == {
        "input_tokens": 12,
        "output_tokens": 2,
        "total_tokens": 14,
        "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 4,
    }
    assert provider._last_finalize_stop_reason == "stop_sequence"
    assert provider._last_finalize_stop_sequence == "END"
    assert provider._last_finalize_message == {
        "role": "assistant",
        "content": "Final answer",
    }
    assert messages_api.create_requests[0] == {
        "model": "claude-test",
        "max_tokens": 99,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Answer"}]}
        ],
        "temperature": 0.0,
    }


@pytest.mark.asyncio
async def test_anthropic_afinalize_stream_is_native_and_remembers_final_message() -> None:
    final_message = _message(
        [SimpleNamespace(type="text", text="Hello async Claude")],
        input_tokens=9,
        output_tokens=4,
    )
    stream = _AsyncMessageStream(final_message)
    messages_api = _AsyncMessages([], stream)
    provider = _provider(messages_api, model="claude-test", temperature=0.1)

    chunks = [
        chunk
        async for chunk in provider.afinalize_stream(
            [{"role": "user", "content": "Hello"}], []
        )
    ]

    assert chunks == ["Hello", " async Claude"]
    assert stream.entered is True
    assert stream.final_message_requested is True
    assert stream.exited is True
    assert messages_api.stream_requests[0] == {
        "model": "claude-test",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
        ],
        "temperature": 0.1,
    }
    assert provider._last_stream_usage == {
        "input_tokens": 9,
        "output_tokens": 4,
        "total_tokens": 13,
        "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 4,
    }
    assert provider._last_finalize_usage == provider._last_stream_usage
    assert provider._last_finalize_message == {
        "role": "assistant",
        "content": "Hello async Claude",
    }


@pytest.mark.asyncio
async def test_anthropic_native_async_preserves_sdk_exceptions() -> None:
    failure = RuntimeError("provider rejected request")
    provider = _provider(_AsyncMessages([failure]))

    with pytest.raises(RuntimeError, match="provider rejected request") as caught:
        await provider.afinalize([{"role": "user", "content": "Answer"}], [])

    assert caught.value is failure
