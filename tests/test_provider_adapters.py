from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.protocol import ToolSpec
from mtp.media import Audio, File, Image, Video
from mtp.providers import (
    AnthropicToolCallingProvider,
    GeminiToolCallingProvider,
    LMStudioToolCallingProvider,
    OpenAIToolCallingProvider,
    OllamaToolCallingProvider,
    OpenRouterToolCallingProvider,
    SambaNovaToolCallingProvider,
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
    def __init__(self, content: str, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


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


class _FakeOpenAIStreamChunk:
    def __init__(self, content: str) -> None:
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]


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


class ProviderAdapterTests(unittest.TestCase):
    def test_openai_like_providers_handle_invalid_tool_json(self) -> None:
        tools = [ToolSpec(name="x.test", description="x", input_schema={"type": "object"})]
        for provider_cls in (
            OpenAIToolCallingProvider,
            OpenRouterToolCallingProvider,
            SambaNovaToolCallingProvider,
            LMStudioToolCallingProvider,
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


if __name__ == "__main__":
    unittest.main()
