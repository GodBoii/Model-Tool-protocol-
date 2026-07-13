from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.protocol import ToolSpec
from mtp.providers.groq_provider import GroqToolCallingProvider


class _AsyncCompletions:
    def __init__(self) -> None:
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="async done", tool_calls=None, reasoning=None))],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5),
        )


@pytest.mark.asyncio
async def test_groq_uses_injected_native_async_client() -> None:
    completions = _AsyncCompletions()
    async_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    sync_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
    provider = GroqToolCallingProvider(client=sync_client, async_client=async_client)

    action = await provider.anext_action(
        [{"role": "user", "content": "hello"}],
        [ToolSpec(name="test.work", description="Work", input_schema={"type": "object"})],
    )
    final = await provider.afinalize([{"role": "user", "content": "hello"}], [])

    assert action.response_text == "async done"
    assert final == "async done"
    assert completions.requests[0]["tools"][0]["function"]["name"] == "test.work"
    assert provider._last_finalize_usage == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    assert provider.capabilities().supports_native_async is True
