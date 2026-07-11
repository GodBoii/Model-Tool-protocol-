from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.protocol import ToolSpec
from mtp.providers.cohere_provider import CohereToolCallingProvider
from mtp.providers.fireworks_provider import FireworksAIToolCallingProvider
from mtp.providers.ollama_provider import OllamaToolCallingProvider
from mtp.providers.openrouter_provider import OpenRouterToolCallingProvider


class _Completions:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        message = SimpleNamespace(content="done", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


class _OpenAIClient:
    def __init__(self) -> None:
        self.completions = _Completions()
        self.chat = SimpleNamespace(completions=self.completions)


class _CohereClient:
    def __init__(self) -> None:
        self.request: dict[str, object] = {}

    def chat(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(
            message=SimpleNamespace(content=[SimpleNamespace(type="text", text="done")], tool_calls=[]),
            usage=None,
        )


class _OllamaClient:
    def __init__(self) -> None:
        self.request: dict[str, object] = {}

    def chat(self, **kwargs):
        self.request = kwargs
        return {"message": {"content": "done"}}


TOOL = ToolSpec(name="test.work", description="Work", input_schema={"type": "object"})
MESSAGES = [{"role": "user", "content": "work"}]


def test_openrouter_forwards_parallel_tools_and_structured_format() -> None:
    client = _OpenAIClient()
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": {"type": "object"}},
    }
    provider = OpenRouterToolCallingProvider(
        client=client,
        parallel_tool_calls=False,
        response_format=response_format,
    )

    provider.next_action(MESSAGES, [TOOL])
    provider.finalize(MESSAGES, [])

    assert client.completions.requests[0]["parallel_tool_calls"] is False
    assert client.completions.requests[0]["response_format"] == response_format
    assert client.completions.requests[1]["response_format"] == response_format
    capabilities = provider.capabilities()
    assert capabilities.supports_parallel_tool_calls is False
    assert capabilities.structured_output_support == "native_json_schema"


def test_cohere_enables_v2_strict_tool_schemas() -> None:
    client = _CohereClient()
    provider = CohereToolCallingProvider(client=client, strict_tools=True)

    provider.next_action(MESSAGES, [TOOL])

    assert client.request["strict_tools"] is True
    assert "strict_tools" not in CohereToolCallingProvider(client=_CohereClient())._to_cohere_messages(MESSAGES)[0]


@pytest.mark.parametrize("think", [True, False, "low", "medium", "high"])
def test_ollama_accepts_documented_thinking_values(think: bool | str) -> None:
    client = _OllamaClient()
    provider = OllamaToolCallingProvider(client=client, think=think)

    provider.next_action(MESSAGES, [])

    assert client.request["think"] == think


def test_ollama_rejects_unknown_thinking_level() -> None:
    with pytest.raises(ValueError, match="low, medium, high"):
        OllamaToolCallingProvider(client=_OllamaClient(), think="maximum")


def test_ollama_reports_native_structured_output_modes() -> None:
    json_provider = OllamaToolCallingProvider(client=_OllamaClient(), format="json")
    schema_provider = OllamaToolCallingProvider(client=_OllamaClient(), format={"type": "object"})

    assert json_provider.capabilities().structured_output_support == "native_json_object"
    assert schema_provider.capabilities().structured_output_support == "native_json_schema"


@pytest.mark.parametrize(
    ("response_format", "expected"),
    [
        ({"type": "json_object"}, "native_json_object"),
        ({"type": "json_schema", "json_schema": {}}, "native_json_schema"),
        ({"type": "text"}, "client_validated"),
    ],
)
def test_fireworks_reports_the_configured_structured_mode(response_format, expected) -> None:
    provider = FireworksAIToolCallingProvider(
        client=_OpenAIClient(),
        response_format=response_format,
    )

    assert provider.capabilities().structured_output_support == expected
