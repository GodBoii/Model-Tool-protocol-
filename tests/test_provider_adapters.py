from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.agent import AgentAction
from mtp.protocol import ToolSpec
from mtp.media import Audio, File, Image, Video
from mtp.providers import (
    AnthropicToolCallingProvider,
    GeminiToolCallingProvider,
    LMStudioToolCallingProvider,
    GroqToolCallingProvider,
    OpenAIToolCallingProvider,
    OllamaToolCallingProvider,
    OpenRouterToolCallingProvider,
    SambaNovaToolCallingProvider,
    XiaomiToolCallingProvider,
)


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = _Fn(name, arguments)


class _OpenAIMessage:
    def __init__(self, content: str, tool_calls=None, reasoning_content: str | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content


class _OpenAIResponse:
    def __init__(self, message: _OpenAIMessage) -> None:
        self.choices = [SimpleNamespace(message=message)]


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.calls = 0

    def _create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return _OpenAIResponse(_OpenAIMessage("", [_ToolCall("c1", "x.test", "{bad json}")]))
        return _OpenAIResponse(_OpenAIMessage("done"))


class _FakeSingleResponseOpenAIClient:
    def __init__(self, message: _OpenAIMessage) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.message = message
        self.calls = []

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return _OpenAIResponse(self.message)


class _FakeOpenAIStreamChunk:
    def __init__(
        self,
        content: str = "",
        *,
        reasoning_content: str | None = None,
        tool_calls: list[Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        delta = SimpleNamespace(content=content or None)
        if reasoning_content is not None:
            delta.reasoning_content = reasoning_content
        if tool_calls is not None:
            delta.tool_calls = tool_calls
        self.choices = [SimpleNamespace(delta=delta)]
        self.usage = usage


class _FakeStreamToolCallDelta:
    def __init__(self, *, index: int, call_id: str | None = None, name: str | None = None, arguments: str | None = None) -> None:
        self.index = index
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=arguments)


class _AnthropicContent:
    def __init__(self, block_type: str, **kwargs) -> None:
        self.type = block_type
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeAnthropicMessages:
    def __init__(self) -> None:
        self.last_kwargs = None
        self.calls = 0

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                content=[
                    _AnthropicContent("text", text="prep"),
                    _AnthropicContent("tool_use", id="tu1", name="x.test", input={"v": 1}),
                ]
            )
        return SimpleNamespace(content=[_AnthropicContent("text", text="final")])


class _FakeAnthropicClient:
    def __init__(self) -> None:
        self.messages = _FakeAnthropicMessages()


class _GeminiFunctionCall:
    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.args = args


class _GeminiPart:
    def __init__(self, function_call=None) -> None:
        self.function_call = function_call


class _GeminiResponse:
    def __init__(self, text: str, parts=None) -> None:
        self.text = text
        self.candidates = [SimpleNamespace(content=SimpleNamespace(parts=parts or []))]


class _FakeGeminiModels:
    def __init__(self) -> None:
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return _GeminiResponse("", [_GeminiPart(_GeminiFunctionCall("x.test", {"a": 1}))])
        return _GeminiResponse("done")


class _FakeGeminiClient:
    def __init__(self) -> None:
        self.models = _FakeGeminiModels()


class _FakeOllamaClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        if kwargs.get("stream"):
            return [
                {"message": {"content": "hello "}},
                {"message": {"content": "world"}, "prompt_eval_count": 5, "eval_count": 2},
            ]
        if self.calls == 1:
            return {
                "message": {
                    "content": "",
                    "thinking": "tool reasoning",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "x.test",
                                "arguments": {"value": 7},
                            }
                        }
                    ],
                },
                "prompt_eval_count": 10,
                "eval_count": 3,
            }
        return {
            "message": {"content": "done"},
            "prompt_eval_count": 11,
            "eval_count": 4,
        }


class _FakeXiaomiClient:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("No fake Xiaomi responses left")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ProviderAdapterTests(unittest.TestCase):
    def test_openai_like_providers_handle_invalid_tool_json(self) -> None:
        tools = [ToolSpec(name="x.test", description="x", input_schema={"type": "object"})]
        for provider_cls in (
            OpenAIToolCallingProvider,
            OpenRouterToolCallingProvider,
            SambaNovaToolCallingProvider,
            LMStudioToolCallingProvider,
            XiaomiToolCallingProvider,
        ):
            provider = provider_cls(client=_FakeOpenAIClient())
            action = provider.next_action(messages=[{"role": "user", "content": "go"}], tools=tools)
            self.assertIsNotNone(action.plan)
            self.assertEqual(action.plan.batches[0].calls[0].arguments, {"_raw_arguments": "{bad json}"})

    def test_lmstudio_finalize_stream_yields_text_chunks(self) -> None:
        fake = _FakeOpenAIClient()
        fake.chat.completions.create = lambda **kwargs: [_FakeOpenAIStreamChunk("hello "), _FakeOpenAIStreamChunk("world")]
        provider = LMStudioToolCallingProvider(client=fake)
        output = "".join(provider.finalize_stream(messages=[{"role": "user", "content": "hi"}], tool_results=[]))
        self.assertEqual(output, "hello world")

    def test_anthropic_preserves_system_and_tool_message_metadata(self) -> None:
        fake = _FakeAnthropicClient()
        provider = AnthropicToolCallingProvider(client=fake)
        action = provider.next_action(
            messages=[
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "use tool"},
            ],
            tools=[ToolSpec(name="x.test", description="x", input_schema={"type": "object"})],
        )
        self.assertIsNotNone(action.plan)
        self.assertIn("assistant_tool_message", action.metadata)
        self.assertEqual(fake.messages.last_kwargs["system"], "rules")

    def test_anthropic_formats_user_media_blocks(self) -> None:
        fake = _FakeAnthropicClient()
        provider = AnthropicToolCallingProvider(client=fake)
        provider.next_action(
            messages=[
                {
                    "role": "user",
                    "content": "inspect",
                    "images": [Image(content=b"\xff\xd8\xff", mime_type="image/jpeg")],
                    "files": [File(content=b"hello", filename="note.txt", mime_type="text/plain")],
                }
            ],
            tools=[],
        )
        sent = fake.messages.last_kwargs["messages"][0]["content"]
        block_types = [block["type"] for block in sent]
        self.assertIn("image", block_types)
        self.assertIn("document", block_types)

    def test_gemini_uses_history_prompt_for_finalize(self) -> None:
        fake = _FakeGeminiClient()
        provider = GeminiToolCallingProvider(client=fake)
        _ = provider.next_action(messages=[{"role": "user", "content": "first"}], tools=[])
        _ = provider.finalize(
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ok"},
                {"role": "tool", "tool_name": "x", "content": {"v": 1}},
            ],
            tool_results=[],
        )
        self.assertGreaterEqual(len(fake.models.calls), 2)
        contents = fake.models.calls[-1]["contents"]
        self.assertIsInstance(contents, list)
        has_tool_result = any(
            getattr(part, "function_response", None) is not None
            for content in contents
            for part in getattr(content, "parts", [])
        )
        self.assertTrue(has_tool_result)

    def test_gemini_sanitizes_mtp_schema_for_function_declarations(self) -> None:
        fake = _FakeGeminiClient()
        provider = GeminiToolCallingProvider(client=fake)
        provider.next_action(
            messages=[{"role": "user", "content": "run"}],
            tools=[
                ToolSpec(
                    name="calculator.add",
                    description="Add two numbers",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "a": {
                                "anyOf": [
                                    {"type": "number"},
                                    {
                                        "type": "object",
                                        "properties": {"$ref": {"type": "string"}},
                                        "required": ["$ref"],
                                        "additionalProperties": False,
                                    },
                                ]
                            }
                        },
                        "required": ["a"],
                        "additionalProperties": False,
                    },
                )
            ],
        )
        first_call = fake.models.calls[0]
        tool_decl = first_call["config"]["tools"][0]["function_declarations"][0]
        params = tool_decl["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertEqual(params["properties"]["a"]["type"], "number")
        self.assertNotIn("additionalProperties", str(params))

    def test_openrouter_formats_multimodal_user_parts(self) -> None:
        provider = OpenRouterToolCallingProvider(client=_FakeOpenAIClient())
        formatted = provider._to_openrouter_messages(  # noqa: SLF001
            [
                {
                    "role": "user",
                    "content": "analyze media",
                    "images": [Image(content=b"\xff\xd8\xff", mime_type="image/jpeg")],
                    "audios": [Audio(content=b"RIFF....WAVEfmt ", format="wav")],
                    "videos": [Video(url="https://example.com/video.mp4", format="mp4")],
                    "files": [File(content=b"%PDF-1.4", filename="note.pdf", mime_type="application/pdf")],
                }
            ]
        )
        parts = formatted[0]["content"]
        part_types = [part["type"] for part in parts]
        self.assertIn("image_url", part_types)
        self.assertIn("input_audio", part_types)
        self.assertIn("video_url", part_types)
        self.assertIn("file", part_types)

    def test_openrouter_formats_text_files_as_text_parts(self) -> None:
        provider = OpenRouterToolCallingProvider(client=_FakeOpenAIClient())
        formatted = provider._to_openrouter_messages(  # noqa: SLF001
            [
                {
                    "role": "user",
                    "content": "read this",
                    "files": [File(content=b"hello world", filename="note.txt", mime_type="text/plain")],
                }
            ]
        )
        parts = formatted[0]["content"]
        text_parts = [part for part in parts if part["type"] == "text"]
        self.assertGreaterEqual(len(text_parts), 2)
        self.assertIn("[file:note.txt]", text_parts[-1]["text"])

    def test_ollama_builds_plan_from_native_tool_calls(self) -> None:
        provider = OllamaToolCallingProvider(client=_FakeOllamaClient(), think=True)
        action = provider.next_action(
            messages=[{"role": "user", "content": "run tool"}],
            tools=[ToolSpec(name="x.test", description="x", input_schema={"type": "object"})],
        )
        self.assertIsNotNone(action.plan)
        assert action.plan is not None
        self.assertEqual(action.plan.batches[0].calls[0].name, "x.test")
        self.assertEqual(action.plan.batches[0].calls[0].arguments, {"value": 7})
        self.assertEqual(action.metadata["reasoning"], "tool reasoning")

    def test_ollama_finalize_stream_yields_text_chunks(self) -> None:
        provider = OllamaToolCallingProvider(client=_FakeOllamaClient())
        output = "".join(provider.finalize_stream(messages=[{"role": "user", "content": "hi"}], tool_results=[]))
        self.assertEqual(output, "hello world")

    def test_xiaomi_next_action_preserves_reasoning_content(self) -> None:
        fake = _FakeXiaomiClient(
            [
                _OpenAIResponse(
                    _OpenAIMessage(
                        "I'll search first.",
                        [_ToolCall("c1", "fs.search", "{\"query\": \"spinner widgets\"}")],
                        reasoning_content="Need to inspect the spinner-related files before answering.",
                    )
                )
            ]
        )
        provider = XiaomiToolCallingProvider(client=fake)
        action = provider.next_action(
            messages=[{"role": "user", "content": "inspect"}],
            tools=[ToolSpec(name="fs.search", description="x", input_schema={"type": "object"})],
        )
        self.assertIsNotNone(action.plan)
        self.assertEqual(action.metadata["tool_call_source"], "native_tool_calls")
        self.assertEqual(action.metadata["raw_tool_call_count"], 1)
        self.assertEqual(action.metadata["derived_batch_count"], 1)
        self.assertEqual(action.metadata["derived_batch_modes"], ["sequential"])
        self.assertEqual(action.metadata["reasoning"], "Need to inspect the spinner-related files before answering.")
        assistant_message = action.metadata["assistant_tool_message"]
        self.assertEqual(
            assistant_message["reasoning_content"],
            "Need to inspect the spinner-related files before answering.",
        )

    def test_xiaomi_stream_next_action_streams_reasoning_and_tool_calls(self) -> None:
        fake = _FakeXiaomiClient(
            [
                [
                    _FakeOpenAIStreamChunk(reasoning_content="Need to search first. "),
                    _FakeOpenAIStreamChunk(content="I'll inspect the project. "),
                    _FakeOpenAIStreamChunk(
                        tool_calls=[
                            _FakeStreamToolCallDelta(
                                index=0,
                                call_id="call_1",
                                name="fs.search",
                                arguments="{\"query\": \"spinner widgets\"}",
                            )
                        ]
                    ),
                    _FakeOpenAIStreamChunk(
                        usage={
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                            "completion_tokens_details": {"reasoning_tokens": 3},
                        }
                    ),
                ]
            ]
        )
        provider = XiaomiToolCallingProvider(client=fake)
        chunks = list(
            provider.stream_next_action(
                messages=[{"role": "user", "content": "inspect"}],
                tools=[ToolSpec(name="fs.search", description="x", input_schema={"type": "object"})],
            )
        )
        self.assertEqual(chunks[0], {"type": "reasoning_chunk", "chunk": "Need to search first. "})
        action = chunks[-1]
        assert isinstance(action, AgentAction)
        self.assertIsNotNone(action.plan)
        self.assertEqual(action.metadata["tool_call_source"], "native_tool_calls")
        self.assertEqual(action.metadata["raw_tool_call_count"], 1)
        self.assertEqual(action.plan.batches[0].calls[0].name, "fs.search")
        self.assertEqual(action.metadata["usage"]["reasoning_tokens"], 3)
        request_args = fake.calls[0]
        self.assertTrue(request_args["stream"])
        self.assertEqual(request_args["stream_options"], {"include_usage": True})
        self.assertEqual(request_args["extra_body"]["thinking"]["type"], "enabled")

    def test_xiaomi_disables_thinking_after_tool_history(self) -> None:
        fake = _FakeXiaomiClient([_OpenAIResponse(_OpenAIMessage("done"))])
        provider = XiaomiToolCallingProvider(client=fake)
        action = provider.next_action(
            messages=[
                {"role": "user", "content": "inspect"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "fs.search", "arguments": "{\"query\":\"spinner\"}"},
                        }
                    ],
                    "reasoning_content": "Prior reasoning",
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "{\"hits\": []}"},
            ],
            tools=[ToolSpec(name="fs.read_text", description="x", input_schema={"type": "object"})],
        )
        self.assertEqual(fake.calls[0]["extra_body"]["thinking"]["type"], "disabled")
        self.assertEqual(action.response_text, "done")
        self.assertIsNone(action.plan)

    def test_xiaomi_enabled_mode_still_disables_tool_rounds_after_tool_history(self) -> None:
        fake = _FakeXiaomiClient([_OpenAIResponse(_OpenAIMessage("done"))])
        provider = XiaomiToolCallingProvider(client=fake, thinking_mode="enabled", final_thinking_mode="enabled")
        provider.next_action(
            messages=[
                {"role": "user", "content": "inspect"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "fs.search", "arguments": "{\"query\":\"spinner\"}"},
                        }
                    ],
                    "reasoning_content": "Prior reasoning",
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "{\"hits\": []}"},
            ],
            tools=[ToolSpec(name="fs.read_text", description="x", input_schema={"type": "object"})],
        )
        self.assertEqual(fake.calls[0]["extra_body"]["thinking"]["type"], "disabled")

    def test_xiaomi_inline_tool_call_text_is_not_parsed(self) -> None:
        fake = _FakeXiaomiClient(
            [
                _OpenAIResponse(
                    _OpenAIMessage(
                        "<tool_call>\n<function=fs.read_text>\n<parameter=path>src/app.py</parameter>\n</function>\n</tool_call>",
                        reasoning_content="Need to open the file before answering.",
                    )
                )
            ]
        )
        provider = XiaomiToolCallingProvider(client=fake)
        action = provider.next_action(
            messages=[{"role": "user", "content": "inspect"}],
            tools=[ToolSpec(name="fs.read_text", description="x", input_schema={"type": "object"})],
        )
        self.assertIsNone(action.plan)
        self.assertIn("<tool_call>", action.response_text or "")
        self.assertNotIn("tool_call_source", action.metadata)

    def test_xiaomi_stream_inline_tool_call_text_streams_as_text(self) -> None:
        fake = _FakeXiaomiClient(
            [
                [
                    _FakeOpenAIStreamChunk(content="I will inspect first."),
                    _FakeOpenAIStreamChunk(
                        content=(
                            "<tool_call><function=fs.read_text>"
                            "<parameter=path>src/app.py</parameter>"
                            "</function></tool_call>"
                        )
                    ),
                ]
            ]
        )
        provider = XiaomiToolCallingProvider(client=fake)

        chunks = list(
            provider.stream_next_action(
                messages=[{"role": "user", "content": "inspect"}],
                tools=[ToolSpec(name="fs.read_text", description="x", input_schema={"type": "object"})],
            )
        )

        self.assertTrue(any(isinstance(chunk, dict) and chunk.get("type") == "text_chunk" for chunk in chunks))
        action = chunks[-1]
        assert isinstance(action, AgentAction)
        self.assertIsNone(action.plan)
        self.assertIn("<tool_call>", action.response_text or "")
        self.assertNotIn("tool_call_source", action.metadata)

    def test_groq_inline_tool_call_text_is_not_parsed(self) -> None:
        fake = _FakeSingleResponseOpenAIClient(
            _OpenAIMessage(
                (
                    "<tool_call><function=file.list_files>"
                    "<parameter=path>.</parameter>"
                    "<parameter=recursive>True</parameter>"
                    "</function></tool_call>"
                )
            )
        )
        provider = GroqToolCallingProvider(client=fake)
        action = provider.next_action(
            messages=[{"role": "user", "content": "list files"}],
            tools=[
                ToolSpec(
                    name="file.list_files",
                    description="list",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "recursive": {"type": "boolean"},
                        },
                    },
                )
            ],
        )

        self.assertIsNone(action.plan)
        self.assertIn("<tool_call>", action.response_text or "")
        self.assertNotIn("tool_call_source", action.metadata)

    def test_groq_inline_bash_alias_text_is_not_parsed(self) -> None:
        fake = _FakeSingleResponseOpenAIClient(
            _OpenAIMessage(
                (
                    "<tool_call><function=bash>"
                    "<parameter=command>dir</parameter>"
                    "<parameter=workdir>C:\\tmp</parameter>"
                    "</function></tool_call>"
                )
            )
        )
        provider = GroqToolCallingProvider(client=fake)
        action = provider.next_action(
            messages=[{"role": "user", "content": "run dir"}],
            tools=[
                ToolSpec(
                    name="shell.run_command",
                    description="run",
                    input_schema={
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                )
            ],
        )

        self.assertIsNone(action.plan)
        self.assertIn("<function=bash>", action.response_text or "")
        self.assertNotIn("tool_call_source", action.metadata)


if __name__ == "__main__":
    unittest.main()
