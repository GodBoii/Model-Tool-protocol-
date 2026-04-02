from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..media import File, Image
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import calls_to_dependency_batches, extract_refs, extract_usage_metrics, normalize_refs, safe_load_arguments


class AnthropicToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for Anthropic Claude.
    Sends tool definitions via the Anthropic Tool-Use API.
    """

    def __init__(
        self,
        *,
        model: str = "claude-3-5-sonnet-20241022",
        api_key: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._last_finalize_usage: dict[str, int] | None = None
        self._client = client or self._make_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "`anthropic` not installed. Please install using `pip install anthropic`"
            ) from exc

        key = api_key or require_env("ANTHROPIC_API_KEY")
        return anthropic.Anthropic(api_key=key)

    def _to_anthropic_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema or {"type": "object", "properties": {}},
            }
            for tool in tools
        ]

    def _to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, default=str)
        except Exception:
            return str(content)

    def _guess_mime(self, name_or_path: str, default: str) -> str:
        guessed = mimetypes.guess_type(name_or_path)[0]
        return guessed or default

    def _image_block(self, image: Image) -> dict[str, Any] | None:
        if image.url:
            return {"type": "image", "source": {"type": "url", "url": image.url}}
        raw = image.get_content_bytes()
        if raw is None:
            return None
        mime = image.mime_type
        if mime is None:
            if image.format:
                mime = f"image/{image.format}"
            elif image.filepath:
                mime = self._guess_mime(str(image.filepath), "image/jpeg")
            else:
                mime = "image/jpeg"
        encoded = base64.b64encode(raw).decode("utf-8")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": encoded},
        }

    def _file_block(self, file: File) -> dict[str, Any] | None:
        if file.url:
            return {
                "type": "document",
                "source": {"type": "url", "url": file.url},
                "citations": {"enabled": True},
            }
        raw = file.get_content_bytes()
        if raw is None:
            return None
        file_name = file.filename
        if file_name is None and file.filepath is not None:
            file_name = Path(str(file.filepath)).name
        mime = file.mime_type or (self._guess_mime(file_name, "application/pdf") if file_name else "application/pdf")
        if mime.startswith("text/") or mime == "application/json":
            return {
                "type": "document",
                "source": {
                    "type": "text",
                    "media_type": "text/plain",
                    "data": raw.decode("utf-8", errors="replace"),
                },
                "citations": {"enabled": True},
            }
        encoded = base64.b64encode(raw).decode("utf-8")
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": mime, "data": encoded},
            "citations": {"enabled": True},
        }

    def _assistant_blocks(self, msg: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        text = self._to_text(msg.get("content", ""))
        if text.strip():
            blocks.append({"type": "text", "text": text})
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                name = function.get("name")
                if not isinstance(name, str) or not name:
                    continue
                raw_arguments = function.get("arguments")
                if isinstance(raw_arguments, str):
                    call_input = safe_load_arguments(raw_arguments)
                elif isinstance(raw_arguments, dict):
                    call_input = raw_arguments
                else:
                    call_input = {}
                call_id = tool_call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    call_id = f"call_{len(blocks)}"
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": call_input,
                    }
                )
        return blocks

    def _to_anthropic_payload(self, messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        system_blocks: list[str] = []
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    system_blocks.append(content)
            elif role == "user":
                blocks: list[dict[str, Any]] = []
                text = self._to_text(msg.get("content", ""))
                if text.strip():
                    blocks.append({"type": "text", "text": text})

                images = msg.get("images")
                if isinstance(images, list):
                    for image in images:
                        if isinstance(image, Image):
                            image_block = self._image_block(image)
                            if image_block is not None:
                                blocks.append(image_block)

                files = msg.get("files")
                if isinstance(files, list):
                    for file in files:
                        if isinstance(file, File):
                            file_block = self._file_block(file)
                            if file_block is not None:
                                blocks.append(file_block)

                audios = msg.get("audios")
                if audios is None:
                    audios = msg.get("audio")
                if isinstance(audios, list) and audios:
                    blocks.append(
                        {
                            "type": "text",
                            "text": f"[audio attachments: {len(audios)} item(s)]",
                        }
                    )

                videos = msg.get("videos")
                if isinstance(videos, list) and videos:
                    blocks.append(
                        {
                            "type": "text",
                            "text": f"[video attachments: {len(videos)} item(s)]",
                        }
                    )

                if not blocks:
                    blocks.append({"type": "text", "text": ""})
                formatted.append({"role": "user", "content": blocks})
            elif role == "assistant":
                blocks = self._assistant_blocks(msg)
                if not blocks:
                    blocks.append({"type": "text", "text": ""})
                formatted.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                tool_content = msg.get("content", "")
                if not isinstance(tool_content, str):
                    tool_content = self._to_text(tool_content)
                formatted.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg["tool_call_id"],
                            "content": tool_content,
                        }
                    ],
                })
        system_prompt = "\n\n".join(system_blocks) if system_blocks else None
        return system_prompt, formatted

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        system_prompt, anthropic_messages = self._to_anthropic_payload(messages)
        anthropic_tools = self._to_anthropic_tools(tools)

        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
            "tools": anthropic_tools if tools else [],
            "temperature": self.temperature,
        }
        if system_prompt:
            request["system"] = system_prompt

        response = self._client.messages.create(
            **request,
        )
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "anthropic", "model": self.model}
        if usage:
            action_meta["usage"] = usage

        calls: list[ToolCall] = []
        serialized_tool_calls: list[dict[str, Any]] = []
        id_by_index: dict[int, str] = {}
        response_text_parts: list[str] = []
        for idx, content in enumerate(response.content):
            if content.type == "text":
                text = getattr(content, "text", None)
                if isinstance(text, str) and text:
                    response_text_parts.append(text)
            elif content.type == "tool_use":
                call_id = content.id or f"call_{idx}"
                id_by_index[idx] = call_id
                raw_input = content.input if isinstance(content.input, dict) else dict(content.input)
                normalized_args = normalize_refs(raw_input, id_by_index)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                calls.append(
                    ToolCall(
                        id=call_id,
                        name=content.name,
                        arguments=normalized_args,
                        depends_on=depends_on,
                    )
                )
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": content.name, "arguments": json.dumps(raw_input)},
                    }
                )
        response_text = "\n".join(response_text_parts).strip()

        if calls:
            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(calls),
                metadata={"provider": "anthropic", "model": self.model}
            )
            return AgentAction(
                plan=plan,
                metadata={
                    **action_meta,
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": response_text,
                        "tool_calls": serialized_tool_calls,
                    },
                },
            )

        return AgentAction(response_text=response_text, metadata=action_meta)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        system_prompt, anthropic_messages = self._to_anthropic_payload(messages)
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
            "temperature": self.temperature,
        }
        if system_prompt:
            request["system"] = system_prompt
        response = self._client.messages.create(**request)
        self._last_finalize_usage = extract_usage_metrics(response) or None
        texts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
        if texts:
            return "\n".join(texts).strip()
        return "Done."

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
