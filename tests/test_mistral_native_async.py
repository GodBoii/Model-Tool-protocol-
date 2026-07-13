from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.providers.mistral_provider import MistralToolCallingProvider


def _response(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
    )


class _Chat:
    def __init__(self) -> None:
        self.requests = []

    async def complete_async(self, **kwargs):
        self.requests.append(kwargs)
        return _response("native")

    async def stream_async(self, **kwargs):
        self.requests.append(kwargs)

        async def chunks():
            yield SimpleNamespace(data=SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="na"))], usage=None
            ))
            yield SimpleNamespace(data=SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="tive"))],
                usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
            ))
        return chunks()


@pytest.mark.asyncio
async def test_mistral_native_async_actions_and_streaming() -> None:
    chat = _Chat()
    provider = MistralToolCallingProvider(client=SimpleNamespace(chat=chat))

    action = await provider.anext_action([{"role": "user", "content": "hi"}], [])
    final = await provider.afinalize([{"role": "user", "content": "hi"}], [])
    streamed = [chunk async for chunk in provider.afinalize_stream(
        [{"role": "user", "content": "hi"}], []
    )]

    assert action.response_text == "native"
    assert final == "native"
    assert streamed == ["na", "tive"]
    assert provider._last_stream_usage == {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}
    assert provider.capabilities().supports_native_async is True
