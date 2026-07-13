from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.protocol import ToolSpec
from mtp.providers.gemini_provider import GeminiToolCallingProvider


class _FailingSyncModels:
    def generate_content(self, **kwargs):
        raise AssertionError("native async path called the synchronous client")

    def generate_content_stream(self, **kwargs):
        raise AssertionError("native async path called the synchronous client")


class _AsyncStream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


class _AsyncModels:
    def __init__(self, *, responses=(), chunks=()) -> None:
        self.responses = list(responses)
        self.chunks = list(chunks)
        self.requests: list[tuple[str, dict[str, object]]] = []

    async def generate_content(self, **kwargs):
        self.requests.append(("generate_content", kwargs))
        return self.responses.pop(0)

    async def generate_content_stream(self, **kwargs):
        self.requests.append(("generate_content_stream", kwargs))
        return _AsyncStream(self.chunks)


def _response(*, text=None, parts=(), prompt=3, output=2, reasoning=1):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=list(parts)))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt,
            candidates_token_count=output,
            total_token_count=prompt + output,
            thoughts_token_count=reasoning,
        ),
    )


def _provider(models: _AsyncModels) -> GeminiToolCallingProvider:
    return GeminiToolCallingProvider(
        model="gemini-test",
        temperature=0.25,
        client=SimpleNamespace(models=_FailingSyncModels()),
        async_client=SimpleNamespace(models=models),
    )


@pytest.mark.asyncio
async def test_gemini_anext_action_is_native_and_preserves_signed_tool_parts() -> None:
    call = SimpleNamespace(name="weather", args={"city": "Pune"})
    part = SimpleNamespace(
        text=None,
        function_call=call,
        thought=True,
        thought_signature=b"opaque-async-signature",
    )
    models = _AsyncModels(responses=[_response(parts=[part])])
    provider = _provider(models)
    tool = ToolSpec(
        name="weather",
        description="Get weather",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "additionalProperties": False,
        },
    )

    action = await provider.anext_action(
        [
            {"role": "system", "content": "Be concise"},
            {"role": "user", "content": "Weather?"},
        ],
        [tool],
    )

    assert action.plan is not None
    assert action.plan.batches[0].calls[0].arguments == {"city": "Pune"}
    assert action.metadata["usage"] == {
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
        "reasoning_tokens": 1,
    }
    history = action.metadata["assistant_tool_message"]
    assert history["gemini_parts"] == [
        {
            "function_call": {"name": "weather", "args": {"city": "Pune"}},
            "thought": True,
            "thought_signature": "b3BhcXVlLWFzeW5jLXNpZ25hdHVyZQ==",
        }
    ]
    _, request = models.requests[0]
    assert request["model"] == "gemini-test"
    assert request["config"]["temperature"] == 0.25
    assert request["config"]["system_instruction"] == "Be concise"
    declaration = request["config"]["tools"][0]["function_declarations"][0]
    assert declaration["parameters"] == {
        "type": "object",
        "properties": {"city": {"type": "string"}},
    }
    assert provider.capabilities().supports_native_async is True


@pytest.mark.asyncio
async def test_gemini_afinalize_uses_client_aio_and_records_usage() -> None:
    models = _AsyncModels(responses=[_response(text="Native answer", prompt=8, output=4)])
    sync_client = SimpleNamespace(
        models=_FailingSyncModels(), aio=SimpleNamespace(models=models)
    )
    provider = GeminiToolCallingProvider(client=sync_client)

    text = await provider.afinalize([{"role": "user", "content": "Answer"}], [])

    assert text == "Native answer"
    assert provider._last_finalize_usage == {
        "input_tokens": 8,
        "output_tokens": 4,
        "total_tokens": 12,
        "reasoning_tokens": 1,
    }
    assert models.requests[0][0] == "generate_content"


@pytest.mark.asyncio
async def test_gemini_afinalize_stream_is_native_and_records_final_usage() -> None:
    models = _AsyncModels(
        chunks=[
            SimpleNamespace(text="Hello ", usage_metadata=None),
            _response(
                text="async",
                parts=[
                    SimpleNamespace(text="checking", thought=True),
                    SimpleNamespace(text="async", thought=False),
                ],
                prompt=6,
                output=2,
                reasoning=0,
            ),
        ]
    )
    provider = _provider(models)
    provider._last_finalize_usage = {"total_tokens": 999}

    chunks = [
        chunk
        async for chunk in provider.afinalize_stream(
            [{"role": "user", "content": "Hello"}], []
        )
    ]

    assert chunks == ["Hello ", "async"]
    assert provider._last_finalize_usage is None
    assert provider._last_stream_usage == {
        "input_tokens": 6,
        "output_tokens": 2,
        "total_tokens": 8,
        "reasoning_tokens": 0,
    }
    assert provider._last_stream_reasoning == "checking"
    assert models.requests[0][0] == "generate_content_stream"
