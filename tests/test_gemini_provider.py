from __future__ import annotations

from types import SimpleNamespace

from mtp.providers.gemini_provider import GeminiToolCallingProvider


class _Models:
    def __init__(self, *, response=None, chunks=()) -> None:
        self.response = response
        self.chunks = chunks
        self.requests: list[dict] = []

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        return self.response

    def generate_content_stream(self, **kwargs):
        self.requests.append(kwargs)
        return iter(self.chunks)


class _Client:
    def __init__(self, *, response=None, chunks=()) -> None:
        self.models = _Models(response=response, chunks=chunks)


def _part(*, text=None, function_call=None, signature=None, thought=None):
    return SimpleNamespace(
        text=text,
        function_call=function_call,
        thought_signature=signature,
        thought=thought,
    )


def test_finalize_stream_uses_native_google_stream_and_preserves_usage() -> None:
    chunks = [
        SimpleNamespace(text="Hello ", usage_metadata=None),
        SimpleNamespace(
            text="world",
            usage_metadata=SimpleNamespace(
                prompt_token_count=4,
                candidates_token_count=2,
                total_token_count=6,
                thoughts_token_count=1,
            ),
        ),
    ]
    client = _Client(chunks=chunks)
    provider = GeminiToolCallingProvider(model="gemini-test", client=client)

    assert list(provider.finalize_stream([{"role": "user", "content": "Hi"}], [])) == [
        "Hello ",
        "world",
    ]
    request = client.models.requests[0]
    assert request["model"] == "gemini-test"
    assert request["config"] == {"temperature": 0.0}
    assert provider._last_stream_usage == {
        "input_tokens": 4,
        "output_tokens": 2,
        "total_tokens": 6,
        "reasoning_tokens": 1,
    }
    caps = provider.capabilities()
    assert caps.supports_finalize_streaming is True
    assert caps.supports_parallel_tool_calls is True


def test_function_call_thought_signature_round_trips_in_history() -> None:
    function_call = SimpleNamespace(name="weather", args={"city": "Pune"})
    response_part = _part(function_call=function_call, signature=b"opaque-signature")
    response = SimpleNamespace(
        text=None,
        usage_metadata=None,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[response_part]))],
    )
    client = _Client(response=response)
    provider = GeminiToolCallingProvider(client=client)

    action = provider.next_action([{"role": "user", "content": "Weather?"}], [])
    history_message = action.metadata["assistant_tool_message"]
    assert history_message["gemini_parts"][0]["thought_signature"] == "b3BhcXVlLXNpZ25hdHVyZQ=="

    contents, _ = provider._to_gemini_payload(
        [
            {"role": "user", "content": "Weather?"},
            history_message,
            {"role": "tool", "tool_name": "weather", "content": {"temp": 30}},
        ]
    )
    restored_call_part = contents[1].parts[0]
    assert restored_call_part.function_call.name == "weather"
    assert restored_call_part.function_call.args == {"city": "Pune"}
    assert restored_call_part.thought_signature == b"opaque-signature"


def test_gemini_history_keeps_signed_parts_separate() -> None:
    provider = GeminiToolCallingProvider(client=_Client())
    contents, _ = provider._to_gemini_payload(
        [
            {
                "role": "assistant",
                "content": "ignored duplicate",
                "gemini_parts": [
                    {"text": "thinking", "thought": True, "thought_signature": "c2lnLTE="},
                    {
                        "function_call": {"name": "first", "args": {}},
                        "thought_signature": "c2lnLTI=",
                    },
                ],
            }
        ]
    )

    assert len(contents[0].parts) == 2
    assert contents[0].parts[0].thought_signature == b"sig-1"
    assert contents[0].parts[1].thought_signature == b"sig-2"
