from __future__ import annotations

from pathlib import Path

import pytest

from mtp.codebase.memory import CodebaseMemory
from mtp.providers.xiaomi_provider import XiaomiToolCallingProvider
from mtp.cli.providers import get_provider
from mtp.cli.tui_state import BACKENDS


def test_codebase_memory_search_does_not_auto_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "sample.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")

    memory = CodebaseMemory(tmp_path)
    memory.scan(enable=True)

    def _unexpected_refresh() -> None:
        raise AssertionError("search should not trigger refresh_changed")

    monkeypatch.setattr(memory, "refresh_changed", _unexpected_refresh)
    hits = memory.search("greet")
    assert hits


def test_tui_state_includes_local_backends() -> None:
    assert "ollama" in BACKENDS
    assert "lmstudio" in BACKENDS


def test_mistral_provider_metadata_uses_mistralai_sdk() -> None:
    provider = get_provider("mistral")
    assert provider is not None
    assert provider.sdk_module == "mistralai"


@pytest.mark.asyncio
async def test_xiaomi_astream_next_action_stops_cleanly() -> None:
    provider = object.__new__(XiaomiToolCallingProvider)

    def _stream_next_action(messages, tools):
        yield {"type": "text_chunk", "chunk": "hello"}

    provider.stream_next_action = _stream_next_action  # type: ignore[method-assign]

    chunks = []
    async for chunk in provider.astream_next_action([], []):
        chunks.append(chunk)

    assert chunks == [{"type": "text_chunk", "chunk": "hello"}]


def test_xiaomi_base_url_can_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIMO_BASE_URL", "https://example.test/v1/")

    provider = XiaomiToolCallingProvider(api_key="test-key", client=object())

    assert provider.base_url == "https://example.test/v1"


def test_xiaomi_parallel_tool_fallback_does_not_mutate_request_args() -> None:
    class _Completions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(dict(kwargs))
            if "parallel_tool_calls" in kwargs:
                raise TypeError("unexpected keyword argument 'parallel_tool_calls'")
            return {"ok": True}

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _Client:
        def __init__(self) -> None:
            self.chat = _Chat()

    client = _Client()
    provider = XiaomiToolCallingProvider(api_key="test-key", client=client)
    request_args = {"model": "mimo", "messages": [], "parallel_tool_calls": True}

    assert provider._create_completion(request_args) == {"ok": True}
    assert request_args["parallel_tool_calls"] is True
    assert "parallel_tool_calls" in client.chat.completions.calls[0]
    assert "parallel_tool_calls" not in client.chat.completions.calls[1]


def test_xiaomi_tool_action_uses_shared_translation_and_preserves_reasoning_content() -> None:
    provider = XiaomiToolCallingProvider(api_key="test-key", client=object())

    action = provider._tool_action_from_calls(
        tool_calls=[
            {
                "id": "call_0",
                "function": {"name": "math.add", "arguments": '{"a": 1, "b": 2}'},
            },
            {
                "id": "call_1",
                "function": {"name": "store.save", "arguments": '{"value": {"$ref": "last"}}'},
            },
        ],
        content="using tools",
        reasoning="reason through the call",
        usage={"total_tokens": 12},
        tool_call_source="native_tool_calls",
    )

    assert action.plan is not None
    assert action.metadata["usage"] == {"total_tokens": 12}
    assert action.metadata["assistant_tool_message"]["reasoning"] == "reason through the call"
    assert action.metadata["assistant_tool_message"]["reasoning_content"] == "reason through the call"
    second_call = action.plan.batches[1].calls[0]
    assert second_call.arguments == {"value": {"$ref": "call_0"}}
    assert second_call.depends_on == ["call_0"]
