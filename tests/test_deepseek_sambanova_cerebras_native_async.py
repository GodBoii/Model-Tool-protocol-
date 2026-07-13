from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.protocol import ToolSpec
from mtp.providers.cerebras_provider import CerebrasToolCallingProvider
from mtp.providers.deepseek_provider import DeepSeekToolCallingProvider
from mtp.providers.sambanova_provider import SambaNovaToolCallingProvider


class _FailingSyncCompletions:
    def create(self, **kwargs):
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


class _AsyncCompletions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def _response(content="done", *, tool_calls=None, reasoning=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=tool_calls,
                    reasoning_content=reasoning,
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3, total_tokens=7),
    )


def _chunk(content=None, *, usage=None):
    choices = [] if content is None else [SimpleNamespace(delta=SimpleNamespace(content=content))]
    return SimpleNamespace(choices=choices, usage=usage)


@pytest.mark.parametrize(
    ("provider_cls", "provider_name", "expects_parallel"),
    [
        (DeepSeekToolCallingProvider, "deepseek", True),
        (SambaNovaToolCallingProvider, "sambanova", False),
        (CerebrasToolCallingProvider, "cerebras", True),
    ],
)
@pytest.mark.asyncio
async def test_compatible_adapters_use_injected_native_async_client(
    provider_cls, provider_name: str, expects_parallel: bool
) -> None:
    function = SimpleNamespace(name="weather.get", arguments='{"city":"Pune"}')
    tool_call = SimpleNamespace(id="call_1", function=function)
    stream = _AsyncStream(
        [
            _chunk("fast "),
            _chunk("answer"),
            _chunk(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7)),
        ]
    )
    completions = _AsyncCompletions(
        [_response("checking", tool_calls=[tool_call], reasoning="thinking"), _response(), stream]
    )
    async_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    sync_client = SimpleNamespace(chat=SimpleNamespace(completions=_FailingSyncCompletions()))
    provider = provider_cls(client=sync_client, async_client=async_client)
    tool = ToolSpec(
        name="weather.get",
        description="Get weather",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
    )

    action = await provider.anext_action([{"role": "user", "content": "weather"}], [tool])
    final = await provider.afinalize([{"role": "user", "content": "summarize"}], [])
    streamed = [
        part
        async for part in provider.afinalize_stream(
            [{"role": "user", "content": "stream"}], []
        )
    ]

    assert action.plan is not None
    assert action.plan.batches[0].calls[0].arguments == {"city": "Pune"}
    assert action.metadata["provider"] == provider_name
    assert action.metadata["usage"] == {
        "input_tokens": 4,
        "output_tokens": 3,
        "total_tokens": 7,
    }
    if provider_name == "deepseek":
        assert action.metadata["reasoning"] == "thinking"
    assert final == "done"
    assert streamed == ["fast ", "answer"]
    assert provider._last_finalize_usage == {
        "input_tokens": 4,
        "output_tokens": 3,
        "total_tokens": 7,
    }
    assert provider._last_stream_usage == {
        "input_tokens": 5,
        "output_tokens": 2,
        "total_tokens": 7,
    }
    planning_request = completions.requests[0]
    assert planning_request["tools"][0]["function"]["name"] == "weather.get"
    assert ("parallel_tool_calls" in planning_request) is expects_parallel
    assert completions.requests[2]["stream"] is True
    if provider_name == "cerebras":
        assert "stream_options" not in completions.requests[2]
    else:
        assert completions.requests[2]["stream_options"] == {"include_usage": True}
    assert provider.capabilities().supports_native_async is True


@pytest.mark.asyncio
async def test_deepseek_reasoner_async_preserves_unsupported_tool_restrictions() -> None:
    completions = _AsyncCompletions([_response(reasoning="private chain")])
    provider = DeepSeekToolCallingProvider(
        model="deepseek-reasoner",
        client=SimpleNamespace(chat=SimpleNamespace(completions=_FailingSyncCompletions())),
        async_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    action = await provider.anext_action(
        [{"role": "user", "content": "reason"}],
        [ToolSpec(name="unsafe.for.reasoner", description="not sent")],
    )

    assert action.response_text == "done"
    assert action.metadata["reasoning"] == "private chain"
    assert "tools" not in completions.requests[0]
    assert "tool_choice" not in completions.requests[0]
    assert "parallel_tool_calls" not in completions.requests[0]
