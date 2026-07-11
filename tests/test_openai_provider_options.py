from __future__ import annotations

from types import SimpleNamespace

from mtp.protocol import ToolSpec
from mtp.providers.common import STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA
from mtp.providers.openai_provider import OpenAIToolCallingProvider


class _Completions:
    def __init__(self) -> None:
        self.request: dict[str, object] = {}
        self.with_raw_response = None

    def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(
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
        response_format=response_format,
        max_completion_tokens=321,
        timeout=12.5,
    )
    tool = ToolSpec(name="test.work", description="Work", input_schema={"type": "object"})

    provider.next_action([{"role": "user", "content": "work"}], [tool])

    assert completions.request["response_format"] == response_format
    assert completions.request["max_completion_tokens"] == 321
    assert completions.request["timeout"] == 12.5
    assert completions.request["tools"][0]["function"]["strict"] is True
    assert provider.capabilities().structured_output_support == STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA
