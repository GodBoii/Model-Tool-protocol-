from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.providers.openai_provider import OpenAIToolCallingProvider
from mtp.providers.openrouter_provider import OpenRouterToolCallingProvider
from mtp.providers.together_provider import TogetherAIToolCallingProvider


class _StreamingCompletions:
    def __init__(self) -> None:
        self.request: dict[str, object] = {}

    def create(self, **kwargs):
        self.request = kwargs
        # This mirrors OpenAI-compatible chat streaming: role-only delta,
        # content deltas, then a usage-only terminal chunk.
        return iter(
            [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(role="assistant", content=None))],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))],
                    usage=None,
                ),
                {"choices": [{"delta": {"content": " world"}}]},
                SimpleNamespace(
                    choices=[],
                    usage=SimpleNamespace(
                        prompt_tokens=7, completion_tokens=2, total_tokens=9
                    ),
                ),
            ]
        )


@pytest.mark.parametrize(
    "provider_type",
    [
        OpenAIToolCallingProvider,
        OpenRouterToolCallingProvider,
        TogetherAIToolCallingProvider,
    ],
)
def test_openai_compatible_finalize_stream_accumulates_deltas_and_usage(provider_type) -> None:
    completions = _StreamingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = provider_type(client=client)

    chunks = list(provider.finalize_stream([{"role": "user", "content": "Hi"}], []))

    assert chunks == ["Hello", " world"]
    assert provider._last_stream_usage == {
        "input_tokens": 7,
        "output_tokens": 2,
        "total_tokens": 9,
    }
    assert completions.request["stream"] is True
    assert completions.request["stream_options"] == {"include_usage": True}
    assert provider.capabilities().supports_finalize_streaming is True


def test_openai_stream_preserves_finalize_request_options() -> None:
    completions = _StreamingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    response_format = {"type": "json_object"}
    provider = OpenAIToolCallingProvider(
        client=client,
        response_format=response_format,
        max_completion_tokens=55,
        timeout=3.0,
    )

    list(provider.finalize_stream([{"role": "user", "content": "Hi"}], []))

    assert completions.request["response_format"] == response_format
    assert completions.request["max_completion_tokens"] == 55
    assert completions.request["timeout"] == 3.0
