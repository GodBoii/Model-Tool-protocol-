"""Native SDK async JSON/SSE, explicit media, and structured output contracts."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace as NS

import pytest

from mtp.agent import AgentAction
from mtp.media import File, Image
from mtp.protocol import ToolSpec
from mtp.providers import DashScope, DeepInfra, Groq, HuggingFace, OpenAIResponses
from mtp.runtime import ToolRegistry

SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


@pytest.fixture
def wire_server():
    requests = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            responses = "input" in request
            history = request.get("input", request.get("messages", []))
            tools = request.get("tools", [])
            call = bool(tools) and not any(
                item.get("role") == "tool" or item.get("type") == "function_call_output"
                for item in history
            )
            text = (
                '{"value":7}'
                if "response_format" in request or "text" in request
                else "done"
            )
            name = (
                (tools[0]["name"] if responses else tools[0]["function"]["name"])
                if call
                else "echo"
            )
            function = {"name": name, "arguments": '{"value":7}'}
            if responses:
                output = [
                    {
                        "type": "reasoning",
                        "id": "r1",
                        "summary": [],
                        "encrypted_content": "opaque",
                    }
                ]
                output.append(
                    {
                        "type": "function_call",
                        "id": "f1",
                        "call_id": "c1",
                        "status": "completed",
                        **function,
                    }
                    if call
                    else {
                        "type": "message",
                        "id": "m1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": text, "annotations": []}
                        ],
                    }
                )
                body = {
                    "id": "response1",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "output": output,
                    "model": request["model"],
                    "error": None,
                    "incomplete_details": None,
                    "parallel_tool_calls": True,
                    "tool_choice": "auto",
                    "tools": [],
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                }
                events = (
                    []
                    if call
                    else [
                        {
                            "type": "response.output_text.delta",
                            "delta": text,
                            "sequence_number": 0,
                            "item_id": "m1",
                            "output_index": 0,
                            "content_index": 0,
                        }
                    ]
                )
                events.append(
                    {
                        "type": "response.completed",
                        "response": body,
                        "sequence_number": 1,
                    }
                )
            else:
                message = {"role": "assistant", "content": "" if call else text}
                if call:
                    message["tool_calls"] = [
                        {"id": "c1", "type": "function", "function": function}
                    ]
                body = {
                    "id": "chat1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": request["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "tool_calls" if call else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                }
                delta = (
                    {"tool_calls": [{"index": 0, **message["tool_calls"][0]}]}
                    if call
                    else {"content": text}
                )
                events = [
                    {
                        "id": "chat1",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": request["model"],
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": None}
                        ],
                    },
                    {
                        "id": "chat1",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": request["model"],
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "tool_calls" if call else "stop",
                            }
                        ],
                        "usage": body["usage"],
                    },
                ]
            data = (
                (
                    "".join("data: " + json.dumps(event) + "\n\n" for event in events)
                    + "data: [DONE]\n\n"
                ).encode()
                if request.get("stream")
                else json.dumps(body).encode()
            )
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/event-stream" if request.get("stream") else "application/json",
            )
            hold = bool(request.get("stream")) and any(
                item.get("content") == "hold" for item in history
            )
            self.send_header(
                "Content-Length", str(len(data) + 1000 if hold else len(data))
            )
            self.end_headers()
            self.wfile.write(data.split(b"\n\n")[0] + b"\n\n" if hold else data)
            self.wfile.flush()
            if hold:
                release.wait(5)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", requests
    release.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.integration
@pytest.mark.parametrize("cls", [HuggingFace, OpenAIResponses, Groq])
@pytest.mark.parametrize("cancel", [False, True])
def test_actual_sdk_sse_response_closes_on_consumer_exit_and_cancellation(
    wire_server, cls, cancel
):
    sdk = pytest.importorskip("groq" if cls is Groq else "openai")
    client_cls = sdk.AsyncGroq if cls is Groq else sdk.AsyncOpenAI
    url, _ = wire_server

    async def run():
        async with client_cls(
            api_key="synthetic", base_url=url, max_retries=0, timeout=3
        ) as client:
            captured = []
            api = (
                client.responses if cls is OpenAIResponses else client.chat.completions
            )
            create = api.create

            async def capture(**kwargs):
                stream = await create(**kwargs)
                captured.append(stream)
                return stream

            api.create = capture
            provider = cls(async_client=client)
            iterator = provider.astream_next_action(
                [{"role": "user", "content": "hold"}], []
            )
            assert (await anext(iterator))["type"] == "text_chunk"
            if cancel:
                task = asyncio.create_task(anext(iterator))
                await asyncio.sleep(0.03)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await iterator.aclose()
            assert captured[0].response.is_closed

    asyncio.run(run())


@pytest.mark.integration
@pytest.mark.parametrize(
    "cls", [HuggingFace, DeepInfra, DashScope, OpenAIResponses, Groq]
)
@pytest.mark.parametrize("stream", [False, True])
def test_real_native_async_sdk_tool_replay_and_finalize_without_threads(
    wire_server, monkeypatch, cls, stream
):
    sdk = pytest.importorskip("groq" if cls is Groq else "openai")
    client_cls = sdk.AsyncGroq if cls is Groq else sdk.AsyncOpenAI
    url, requests = wire_server

    real_to_thread = asyncio.to_thread

    async def forbidden(function, *args, **kwargs):
        # The SDK collects platform metadata asynchronously. It is not request fallback.
        if function.__name__ == "get_platform" and function.__module__.split(".")[
            0
        ] in {"openai", "groq"}:
            return await real_to_thread(function, *args, **kwargs)
        pytest.fail("Native adapter path used asyncio.to_thread.")

    async def run():
        async with client_cls(
            api_key="synthetic", base_url=url, max_retries=0, timeout=3
        ) as client:
            provider = cls(async_client=client)
            assert provider.capabilities().supports_native_async
            registry = ToolRegistry()
            registry.register_tool(
                ToolSpec("math.echo", "echo", SCHEMA), lambda value: value
            )
            # The test tool is sync and intentionally handled by the runtime.
            tools = [ToolSpec("math.echo", "echo", SCHEMA)]
            if stream:
                items = [
                    item
                    async for item in provider.astream_next_action(
                        [{"role": "user", "content": "echo"}], tools
                    )
                ]
                action = next(item for item in items if isinstance(item, AgentAction))
            else:
                action = await provider.anext_action(
                    [{"role": "user", "content": "echo"}], tools
                )
            assert action.plan.batches[0].calls[0].name == "math.echo"
            assert action.plan.batches[0].calls[0].arguments == {"value": 7}
            history = [
                action.metadata["assistant_tool_message"],
                {"role": "tool", "tool_call_id": "c1", "content": 7},
            ]
            if stream:
                assert (
                    "".join(
                        [part async for part in provider.afinalize_stream(history, [])]
                    )
                    == "done"
                )
            else:
                assert await provider.afinalize(history, []) == "done"
            assert len(requests) == 2
            if cls is OpenAIResponses:
                assert any(
                    item.get("encrypted_content") == "opaque"
                    for item in requests[1]["input"]
                )

    monkeypatch.setattr(asyncio, "to_thread", forbidden)
    asyncio.run(run())


class AsyncStream:
    def __init__(self, items, *, block=False):
        self.items = iter(items)
        self.block = block
        self.closed = False
        self.started = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        try:
            return next(self.items)
        except StopIteration:
            raise StopAsyncIteration from None

    async def close(self):
        self.closed = True


@pytest.mark.parametrize("cls", [HuggingFace, OpenAIResponses, Groq])
@pytest.mark.parametrize("cancel", [False, True])
def test_native_stream_consumer_close_and_task_cancel_release_stream(cls, cancel):
    async def run():
        chunk = (
            {"type": "response.output_text.delta", "delta": "first"}
            if cls is OpenAIResponses
            else {"choices": [{"delta": {"content": "first"}, "finish_reason": None}]}
        )
        stream = AsyncStream([chunk], block=cancel)

        async def create(**kwargs):
            return stream

        client = NS(responses=NS(create=create), chat=NS(completions=NS(create=create)))
        provider = cls(async_client=client)
        iterator = provider.astream_next_action([], [])
        if cancel:
            task = asyncio.create_task(anext(iterator))
            await stream.started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await anext(iterator))["chunk"] == "first"
            await iterator.aclose()
        assert stream.closed

    asyncio.run(run())


def test_responses_opt_in_media_native_wire_shape_without_remote_fetch(tmp_path):
    path = tmp_path / "document.pdf"
    path.write_bytes(b"%PDF-test")
    provider = OpenAIResponses(
        client=NS(), enable_multimodal=True, input_modalities=("text", "image", "file")
    )
    request = provider._request(
        [
            {
                "role": "user",
                "content": "inspect",
                "images": [Image(content=b"png", mime_type="image/png", detail="low")],
                "files": [
                    File(filepath=path),
                    File(id="file-uploaded"),
                    File(url="https://example.com/file.pdf"),
                ],
            }
        ],
        [],
    )
    content = request["input"][0]["content"]
    assert content[1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,cG5n",
        "detail": "low",
    }
    assert content[2]["filename"] == "document.pdf" and content[2][
        "file_data"
    ].startswith("data:application/pdf;base64,")
    assert content[3] == {"type": "input_file", "file_id": "file-uploaded"}
    assert content[4] == {
        "type": "input_file",
        "file_url": "https://example.com/file.pdf",
    }
    assert provider.capabilities().input_modalities == ["text", "image", "file"]


@pytest.mark.parametrize("cls", [HuggingFace, OpenAIResponses])
@pytest.mark.parametrize(
    "text", ['{"value":7}', '{"value":"wrong"}', '{"value":NaN}', "refusal"]
)
def test_native_structured_output_enforces_local_schema(cls, text):
    pytest.importorskip("jsonschema")
    provider = cls(client=NS(), output_schema=SCHEMA)
    request = provider._request([], [])
    config = (
        request["text"]["format"]
        if cls is OpenAIResponses
        else request["response_format"]["json_schema"]
    )
    assert config["schema"] == SCHEMA and config["strict"] is True
    assert provider.capabilities().structured_output_support == "native_json_schema"
    if text == '{"value":7}':
        assert provider._action(text, [], None, {}).metadata["structured_output"] == {
            "value": 7
        }
    else:
        with pytest.raises(ValueError, match="JSON schema"):
            provider._action(text, [], None, {})


@pytest.mark.integration
@pytest.mark.parametrize("cls", [HuggingFace, OpenAIResponses])
def test_native_async_sdk_serializes_output_schema_and_media(wire_server, cls):
    openai = pytest.importorskip("openai")
    pytest.importorskip("jsonschema")
    url, requests = wire_server

    async def run():
        async with openai.AsyncOpenAI(
            api_key="synthetic", base_url=url, max_retries=0
        ) as client:
            opts = (
                {
                    "enable_multimodal": True,
                    "input_modalities": ("text", "image", "file"),
                }
                if cls is OpenAIResponses
                else {"input_modalities": ("text", "image")}
            )
            provider = cls(async_client=client, output_schema=SCHEMA, **opts)
            action = await provider.anext_action(
                [
                    {
                        "role": "user",
                        "content": "inspect",
                        "images": [Image(url="https://example.com/a.png")],
                    }
                ],
                [],
            )
            assert action.metadata["structured_output"] == {"value": 7}
            serialized = (
                requests[0]["input"][0]["content"]
                if cls is OpenAIResponses
                else requests[0]["messages"][0]["content"]
            )
            assert len(serialized) == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "ref", ["https://example.com/schema.json", "file:///secret.json"]
)
def test_output_schema_cannot_fetch_external_refs(ref):
    with pytest.raises(ValueError, match="local JSON references"):
        HuggingFace(client=NS(), output_schema={"$ref": ref})


@pytest.mark.parametrize(
    "url",
    [
        "file:///secret",
        "https://user:key@example.com/a.pdf",
        "https://exam\nple.com/a.pdf",
    ],
)
def test_responses_opt_in_media_rejects_unsafe_urls_without_fetching(url):
    provider = OpenAIResponses(
        client=NS(), enable_multimodal=True, input_modalities=("text", "image", "file")
    )
    with pytest.raises(ValueError):
        provider._request(
            [{"role": "user", "content": "inspect", "files": [File(url=url)]}], []
        )


def test_model_hints_are_exact_and_do_not_enable_capabilities_automatically():
    from mtp.providers.model_profiles import model_capability_hints

    assert model_capability_hints("openai_responses", "gpt-4o").json_schema is True
    assert (
        model_capability_hints("groq", "openai/gpt-oss-20b").parallel_tool_calls
        is False
    )
    assert model_capability_hints("openai_responses", "gpt-latest") is None
    assert model_capability_hints("custom", "gpt-4o") is None
    provider = OpenAIResponses(client=NS(), model="gpt-4o")
    assert provider.capabilities().input_modalities == ["text"]
    assert provider.capabilities().structured_output_support == "client_validated"


@pytest.mark.integration
@pytest.mark.parametrize("cls", [HuggingFace, OpenAIResponses, Groq])
def test_async_agent_native_tool_and_final_stream_end_to_end(
    wire_server, monkeypatch, cls
):
    from mtp.agent import Agent

    sdk = pytest.importorskip("groq" if cls is Groq else "openai")
    client_cls = sdk.AsyncGroq if cls is Groq else sdk.AsyncOpenAI
    url, requests = wire_server

    async def echo(value):
        return value

    async def run():
        async with client_cls(
            api_key="synthetic", base_url=url, max_retries=0
        ) as client:
            provider = cls(async_client=client)
            provider.next_action = lambda *args: pytest.fail(
                "Agent used synchronous planning."
            )
            provider.finalize_stream = lambda *args: pytest.fail(
                "Agent used synchronous finalization stream."
            )
            registry = ToolRegistry()
            registry.register_tool(ToolSpec("math.echo", "echo", SCHEMA), echo)
            agent = Agent(provider=provider, tools=registry)
            events = [
                event
                async for event in agent.arun_loop_events(
                    "echo 7", max_rounds=1, stream_final=True
                )
            ]
            assert any(
                event.get("type") == "tool_finished" and event.get("output") == 7
                for event in events
            )
            assert any(event.get("type") == "run_completed" for event in events)
            assert any(
                event.get("type") == "text_chunk" and event.get("chunk") == "done"
                for event in events
            )
            assert len(requests) == 2 and requests[-1]["stream"] is True

    asyncio.run(run())


@pytest.mark.parametrize("cls", [HuggingFace, OpenAIResponses, Groq])
def test_async_only_provider_rejects_sync_calls_clearly(cls):
    provider = cls(async_client=NS())
    assert provider.capabilities().supports_native_async
    with pytest.raises(RuntimeError, match="only an async_client"):
        provider.next_action([], [])


def test_provider_closes_only_owned_sdk_clients(monkeypatch):
    openai = pytest.importorskip("openai")
    clients = []

    class SyncClient:
        closed = False

        def __init__(self, **kwargs):
            clients.append(self)

        def close(self):
            self.closed = True

    class NativeClient:
        closed = False

        def __init__(self, **kwargs):
            clients.append(self)

        async def close(self):
            self.closed = True

    monkeypatch.setattr(openai, "OpenAI", SyncClient)
    monkeypatch.setattr(openai, "AsyncOpenAI", NativeClient)

    async def run():
        owned = HuggingFace(api_key="synthetic", native_async=True)
        await owned.aclose()
        assert len(clients) == 2 and all(client.closed for client in clients)
        injected = NativeClient()
        provider = HuggingFace(async_client=injected)
        await provider.aclose()
        assert not injected.closed

    asyncio.run(run())
