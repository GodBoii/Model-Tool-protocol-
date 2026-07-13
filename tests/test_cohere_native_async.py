from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.protocol import ToolSpec
from mtp.providers.cohere_provider import CohereToolCallingProvider


class _AsyncCohereClient:
    def __init__(self, responses: list[object], events: list[object] | None = None) -> None:
        self.responses = iter(responses)
        self.events = events or []
        self.chat_requests: list[dict[str, object]] = []
        self.stream_requests: list[dict[str, object]] = []

    async def chat(self, **kwargs):
        self.chat_requests.append(kwargs)
        return next(self.responses)

    async def chat_stream(self, **kwargs):
        self.stream_requests.append(kwargs)

        async def iterate():
            for event in self.events:
                yield event

        return iterate()


def _response(*, text: str, tool_calls=None, input_tokens: int = 2, output_tokens: int = 3):
    return SimpleNamespace(
        message=SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            tool_calls=tool_calls,
        ),
        usage=SimpleNamespace(
            tokens=SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            billed_units=None,
        ),
    )


@pytest.mark.asyncio
async def test_cohere_uses_injected_native_async_client_for_action_and_finalize() -> None:
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="files__read", arguments='{"path":"README.md"}'),
    )
    client = _AsyncCohereClient(
        [_response(text="I will inspect it.", tool_calls=[tool_call]), _response(text="Done.")]
    )
    provider = CohereToolCallingProvider(
        client=object(),
        async_client=client,
        model="command-a-03-2025",
        temperature=0.2,
        max_tokens=321,
        preamble="Be concise.",
        strict_tools=True,
    )
    tool = ToolSpec(
        name="files.read",
        description="Read a file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )

    action = await provider.anext_action(
        [{"role": "user", "content": "Read the file"}], [tool]
    )
    final = await provider.afinalize(
        [{"role": "user", "content": "Summarize it"}], []
    )

    assert action.plan is not None
    assert action.plan.batches[0].calls[0].name == "files.read"
    assert action.metadata["usage"] == {
        "input_tokens": 2,
        "output_tokens": 3,
        "total_tokens": 5,
    }
    assert final == "Done."
    assert provider._last_finalize_usage == {
        "input_tokens": 2,
        "output_tokens": 3,
        "total_tokens": 5,
    }
    assert client.chat_requests[0] == {
        "model": "command-a-03-2025",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Read the file"},
        ],
        "temperature": 0.2,
        "max_tokens": 321,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "files__read",
                    "description": "Read a file",
                    "parameters": tool.input_schema,
                },
            }
        ],
        "strict_tools": True,
    }
    assert provider.capabilities().supports_native_async is True


@pytest.mark.asyncio
async def test_cohere_native_async_stream_preserves_events_usage_and_options() -> None:
    events = [
        {
            "type": "content-delta",
            "delta": {"message": {"content": {"text": "Hello"}}},
        },
        SimpleNamespace(
            type="content-delta",
            delta=SimpleNamespace(
                message=SimpleNamespace(content=SimpleNamespace(text=" async"))
            ),
        ),
        {
            "type": "message-end",
            "delta": {
                "finish_reason": "COMPLETE",
                "usage": {
                    "tokens": {"input_tokens": 7, "output_tokens": 2},
                    "billed_units": {"input_tokens": 4, "output_tokens": 2},
                },
            },
        },
    ]
    client = _AsyncCohereClient([], events)
    provider = CohereToolCallingProvider(
        client=object(),
        async_client=client,
        model="command-a-03-2025",
        temperature=0.1,
        max_tokens=99,
        preamble="Be brief.",
    )

    chunks = [
        chunk
        async for chunk in provider.afinalize_stream(
            [{"role": "user", "content": "Hello"}], []
        )
    ]

    assert chunks == ["Hello", " async"]
    assert client.stream_requests == [
        {
            "model": "command-a-03-2025",
            "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hello"},
            ],
            "temperature": 0.1,
            "max_tokens": 99,
        }
    ]
    assert provider._last_stream_usage == {
        "input_tokens": 7,
        "output_tokens": 2,
        "total_tokens": 9,
        "billed_input_tokens": 4,
        "billed_output_tokens": 2,
    }
    assert provider._last_finalize_usage == provider._last_stream_usage
    assert provider._last_stream_finish_reason == "COMPLETE"


@pytest.mark.asyncio
async def test_cohere_native_async_preserves_sdk_errors() -> None:
    class CohereFailure(RuntimeError):
        pass

    class FailingClient:
        async def chat(self, **kwargs):
            del kwargs
            raise CohereFailure("rate limited")

    provider = CohereToolCallingProvider(client=object(), async_client=FailingClient())

    with pytest.raises(CohereFailure, match="rate limited"):
        await provider.anext_action([{"role": "user", "content": "Hi"}], [])
