from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.media import File, Image
from mtp.protocol import ToolSpec
from mtp.providers.anthropic_provider import AnthropicToolCallingProvider


class _Messages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.response


def _response(*blocks: object) -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason="end_turn",
        stop_sequence=None,
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            output_tokens_details=SimpleNamespace(thinking_tokens=3),
        ),
    )


def test_anthropic_strict_tools_choice_and_structured_outputs_are_native() -> None:
    response = _response(SimpleNamespace(type="text", text='{"ok":true}'))
    messages = _Messages(response)
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    provider = AnthropicToolCallingProvider(
        client=SimpleNamespace(messages=messages),
        strict_tools=True,
        tool_choice="required",
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    tool = ToolSpec(name="check", description="Check", input_schema=schema)

    provider.next_action([{"role": "user", "content": "Check"}], [tool])

    request = messages.requests[0]
    assert request["tool_choice"] == {"type": "any"}
    assert request["tools"][0]["strict"] is True
    assert request["output_config"] == {
        "format": {"type": "json_schema", "schema": schema}
    }
    assert provider.capabilities().structured_output_support == "native_json_schema"


def test_anthropic_thinking_omits_incompatible_temperature_and_preserves_blocks() -> None:
    thinking = SimpleNamespace(
        type="thinking", thinking="Consider tools", signature="signed-thinking"
    )
    tool_use = SimpleNamespace(type="tool_use", id="toolu_1", name="lookup", input={})
    response = _response(thinking, tool_use)
    messages = _Messages(response)
    provider = AnthropicToolCallingProvider(
        client=SimpleNamespace(messages=messages),
        thinking={"type": "adaptive"},
        temperature=0.7,
    )

    action = provider.next_action(
        [{"role": "user", "content": "Look it up"}],
        [ToolSpec(name="lookup", description="Lookup")],
    )

    assert "temperature" not in messages.requests[0]
    assert messages.requests[0]["thinking"] == {"type": "adaptive"}
    assert action.metadata["usage"]["reasoning_tokens"] == 3
    assistant_message = action.metadata["assistant_tool_message"]
    assert assistant_message["anthropic_content"][0] == {
        "type": "thinking",
        "thinking": "Consider tools",
        "signature": "signed-thinking",
    }
    _, round_tripped = provider._to_anthropic_payload([assistant_message])
    assert round_tripped[0]["content"][0] == assistant_message["anthropic_content"][0]
    assert provider.capabilities().supports_reasoning_metadata is True


def test_anthropic_structured_output_disables_document_citations() -> None:
    provider = AnthropicToolCallingProvider(
        client=SimpleNamespace(messages=_Messages(_response())),
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {"type": "object", "additionalProperties": False},
            }
        },
    )
    block = provider._file_block(File(content=b"hello", mime_type="text/plain"))

    assert block is not None
    assert "citations" not in block


@pytest.mark.parametrize(
    ("image", "message"),
    [
        (Image(content=b"not a png", mime_type="image/png"), "does not match"),
        (Image(content=b"data", mime_type="image/svg+xml"), "Unsupported"),
    ],
)
def test_anthropic_rejects_mislabeled_or_unsupported_images(
    image: Image, message: str
) -> None:
    provider = AnthropicToolCallingProvider(
        client=SimpleNamespace(messages=_Messages(_response()))
    )

    with pytest.raises(ValueError, match=message):
        provider._image_block(image)


def test_anthropic_rejects_non_pdf_binary_documents_and_unsafe_urls() -> None:
    provider = AnthropicToolCallingProvider(
        client=SimpleNamespace(messages=_Messages(_response()))
    )

    with pytest.raises(ValueError, match="valid PDF signature"):
        provider._file_block(File(content=b"not pdf", mime_type="application/pdf"))
    with pytest.raises(ValueError, match="HTTP\\(S\\)"):
        provider._image_block(Image(url="file:///etc/passwd"))


def test_anthropic_rejects_invalid_native_controls_early() -> None:
    client = SimpleNamespace(messages=_Messages(_response()))
    with pytest.raises(ValueError, match="tool_choice"):
        AnthropicToolCallingProvider(client=client, tool_choice="sometimes")
    with pytest.raises(ValueError, match="output_config"):
        AnthropicToolCallingProvider(client=client, output_config={"type": "json_schema"})
