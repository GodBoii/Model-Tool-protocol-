from __future__ import annotations

from types import SimpleNamespace

from mtp.providers.anthropic_provider import AnthropicToolCallingProvider


class _MessageStream:
    """Small mock of anthropic.lib.streaming.MessageStream."""

    def __init__(self, final_message: SimpleNamespace) -> None:
        self.text_stream = iter(["Hello", "", " from Claude"])
        self._final_message = final_message
        self.entered = False
        self.exited = False
        self.final_message_requested = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.exited = True

    def get_final_message(self):
        self.final_message_requested = True
        return self._final_message


class _Messages:
    def __init__(self, message_stream: _MessageStream) -> None:
        self.message_stream = message_stream
        self.request: dict[str, object] = {}

    def stream(self, **kwargs):
        self.request = kwargs
        return self.message_stream


def _client_and_stream():
    # These fields mirror the official Anthropic Message/content-block/Usage
    # shapes returned by MessageStream.get_final_message().
    final_message = SimpleNamespace(
        type="message",
        role="assistant",
        content=[SimpleNamespace(type="text", text="Hello from Claude")],
        model="claude-sonnet-4-6",
        stop_reason="end_turn",
        stop_sequence=None,
        usage=SimpleNamespace(
            input_tokens=11,
            output_tokens=3,
            cache_creation_input_tokens=5,
            cache_read_input_tokens=7,
        ),
    )
    stream = _MessageStream(final_message)
    messages = _Messages(stream)
    return SimpleNamespace(messages=messages), messages, stream


def test_anthropic_finalize_stream_uses_native_sdk_helper_and_terminal_message() -> None:
    client, messages_api, stream = _client_and_stream()
    provider = AnthropicToolCallingProvider(
        client=client,
        model="claude-sonnet-4-6",
        max_tokens=321,
        temperature=0.25,
    )
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Hello"},
    ]

    chunks = list(provider.finalize_stream(messages, []))

    assert chunks == ["Hello", " from Claude"]
    assert stream.entered is True
    assert stream.final_message_requested is True
    assert stream.exited is True
    assert messages_api.request == {
        "model": "claude-sonnet-4-6",
        "max_tokens": 321,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
        ],
        "temperature": 0.25,
        "system": "Be concise.",
    }

    assert provider._last_stream_usage == {
        "input_tokens": 11,
        "output_tokens": 3,
        "total_tokens": 14,
        "cache_creation_input_tokens": 5,
        "cache_read_input_tokens": 7,
    }
    assert provider._last_finalize_usage == provider._last_stream_usage
    assert provider._last_stream_stop_reason == "end_turn"
    assert provider._last_stream_stop_sequence is None
    assert provider._last_finalize_stop_reason == "end_turn"
    assert provider._last_finalize_message == {
        "role": "assistant",
        "content": "Hello from Claude",
    }


def test_anthropic_reports_native_finalize_streaming() -> None:
    client, _, _ = _client_and_stream()
    provider = AnthropicToolCallingProvider(client=client)

    assert provider.capabilities().supports_finalize_streaming is True

