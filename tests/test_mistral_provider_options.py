from __future__ import annotations

from types import SimpleNamespace

from mtp.protocol import ToolSpec
from mtp.providers.mistral_provider import MistralToolCallingProvider


class _Chat:
    def __init__(self) -> None:
        self.request: dict[str, object] = {}

    def complete(self, **kwargs):
        self.request = kwargs
        message = SimpleNamespace(content="done", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def test_mistral_sends_and_reports_parallel_tool_call_setting() -> None:
    chat = _Chat()
    provider = MistralToolCallingProvider(
        client=SimpleNamespace(chat=chat),
        parallel_tool_calls=True,
    )
    tool = ToolSpec(name="test.work", description="Work", input_schema={"type": "object"})

    provider.next_action([{"role": "user", "content": "work"}], [tool])

    assert chat.request["parallel_tool_calls"] is True
    assert provider.capabilities().supports_parallel_tool_calls is True


def test_mistral_disables_parallel_tool_calls_when_configured() -> None:
    provider = MistralToolCallingProvider(
        client=SimpleNamespace(chat=_Chat()),
        parallel_tool_calls=False,
    )

    assert provider.capabilities().supports_parallel_tool_calls is False
