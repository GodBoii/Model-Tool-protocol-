from __future__ import annotations

from types import SimpleNamespace

from mtp.providers.cohere_provider import CohereToolCallingProvider


class _CohereClient:
    def __init__(self, events) -> None:
        self.events = events
        self.request: dict[str, object] = {}

    def chat_stream(self, **kwargs):
        self.request = kwargs
        return iter(self.events)


def _object_events():
    return [
        SimpleNamespace(type="message-start", delta=SimpleNamespace()),
        SimpleNamespace(
            type="content-delta",
            delta=SimpleNamespace(
                message=SimpleNamespace(content=SimpleNamespace(text="Hello"))
            ),
        ),
        SimpleNamespace(
            type="content-delta",
            delta=SimpleNamespace(
                message=SimpleNamespace(content=SimpleNamespace(text=" from Cohere"))
            ),
        ),
        SimpleNamespace(type="content-end", delta=SimpleNamespace()),
        SimpleNamespace(
            type="message-end",
            delta=SimpleNamespace(
                finish_reason="COMPLETE",
                usage=SimpleNamespace(
                    tokens=SimpleNamespace(input_tokens=71.0, output_tokens=3.0),
                    billed_units=SimpleNamespace(input_tokens=5.0, output_tokens=3.0),
                ),
            ),
        ),
    ]


def test_cohere_finalize_stream_uses_v2_events_and_preserves_usage() -> None:
    client = _CohereClient(_object_events())
    provider = CohereToolCallingProvider(
        client=client,
        model="command-a-03-2025",
        temperature=0.2,
        max_tokens=321,
        preamble="Be concise.",
    )

    chunks = list(provider.finalize_stream([{"role": "user", "content": "Hello"}], []))

    assert chunks == ["Hello", " from Cohere"]
    assert client.request == {
        "model": "command-a-03-2025",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hello"},
        ],
        "temperature": 0.2,
        "max_tokens": 321,
    }
    assert provider._last_stream_usage == {
        "input_tokens": 71,
        "output_tokens": 3,
        "total_tokens": 74,
        "billed_input_tokens": 5,
        "billed_output_tokens": 3,
    }
    assert provider._last_finalize_usage == provider._last_stream_usage
    assert provider._last_stream_finish_reason == "COMPLETE"
    assert provider.capabilities().supports_finalize_streaming is True


def test_cohere_finalize_stream_accepts_decoded_event_dicts() -> None:
    events = [
        {
            "type": "content-delta",
            "delta": {"message": {"content": {"text": "Hi"}}},
        },
        {
            "type": "message-end",
            "delta": {
                "finish_reason": "MAX_TOKENS",
                "usage": {
                    "billed_units": {"input_tokens": 2, "output_tokens": 1}
                },
            },
        },
    ]
    provider = CohereToolCallingProvider(client=_CohereClient(events))

    assert list(provider.finalize_stream([{"role": "user", "content": "Hi"}], [])) == ["Hi"]
    assert provider._last_stream_usage == {
        "billed_input_tokens": 2,
        "billed_output_tokens": 1,
        "input_tokens": 2,
        "output_tokens": 1,
        "total_tokens": 3,
    }
    assert provider._last_stream_finish_reason == "MAX_TOKENS"


def test_cohere_new_stream_clears_stale_terminal_metadata() -> None:
    provider = CohereToolCallingProvider(client=_CohereClient([]))
    provider._last_finalize_usage = {"input_tokens": 99}
    provider._last_stream_usage = {"input_tokens": 99}
    provider._last_stream_finish_reason = "COMPLETE"

    assert list(provider.finalize_stream([{"role": "user", "content": "Hi"}], [])) == []
    assert provider._last_finalize_usage is None
    assert provider._last_stream_usage is None
    assert provider._last_stream_finish_reason is None
