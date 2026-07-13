from __future__ import annotations

from types import SimpleNamespace

from mtp.protocol import ToolSpec
from mtp.providers.common import STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA
from mtp.providers.openai_provider import OpenAIToolCallingProvider


class _Completions:
    def __init__(self, response: object | None = None) -> None:
        self.request: dict[str, object] = {}
        self.with_raw_response = None
        self.response = response

    def create(self, **kwargs):
        self.request = kwargs
        return self.response or SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))],
            usage=None,
        )


def test_openai_forwards_modern_request_options_and_strict_tools() -> None:
    completions = _Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": {"type": "object"}},
    }
    provider = OpenAIToolCallingProvider(
        client=client,
        strict_tools=True,
        tool_choice={"type": "function", "function": {"name": "test.work"}},
        parallel_tool_calls=False,
        response_format=response_format,
        reasoning_effort="high",
        max_completion_tokens=321,
        timeout=12.5,
    )
    tool = ToolSpec(name="test.work", description="Work", input_schema={"type": "object"})

    provider.next_action([{"role": "user", "content": "work"}], [tool])

    assert completions.request["response_format"] == response_format
    assert completions.request["reasoning_effort"] == "high"
    assert completions.request["max_completion_tokens"] == 321
    assert completions.request["timeout"] == 12.5
    assert completions.request["tools"][0]["function"]["strict"] is True
    assert completions.request["tool_choice"] == {
        "type": "function",
        "function": {"name": "test.work"},
    }
    assert completions.request["parallel_tool_calls"] is False
    assert provider.capabilities().structured_output_support == STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA
    assert provider.capabilities().supports_parallel_tool_calls is False


def test_openai_omits_optional_sampling_and_empty_stream_options() -> None:
    completions = _Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = OpenAIToolCallingProvider(
        client=client,
        temperature=None,
        stream_include_usage=False,
    )

    provider.next_action([{"role": "user", "content": "reason"}], [])
    assert "temperature" not in completions.request

    request = provider._stream_request_args([{"role": "user", "content": "reason"}])
    assert "temperature" not in request
    assert "stream_options" not in request


def test_openai_stream_options_can_disable_obfuscation_without_usage() -> None:
    provider = OpenAIToolCallingProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=_Completions())),
        stream_include_usage=False,
        stream_include_obfuscation=False,
    )

    request = provider._stream_request_args([{"role": "user", "content": "go"}])

    assert request["stream_options"] == {"include_obfuscation": False}


def test_openai_nonstream_action_preserves_reasoning_token_usage_without_text() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="answer",
                    tool_calls=None,
                    reasoning_content="undocumented private text",
                )
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=5,
            completion_tokens=7,
            total_tokens=12,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
        ),
    )
    completions = _Completions(response)
    provider = OpenAIToolCallingProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )

    action = provider.next_action([{"role": "user", "content": "reason"}], [])

    assert action.response_text == "answer"
    assert "reasoning" not in action.metadata
    assert action.metadata["usage"] == {
        "input_tokens": 5,
        "output_tokens": 7,
        "total_tokens": 12,
        "reasoning_tokens": 4,
    }
