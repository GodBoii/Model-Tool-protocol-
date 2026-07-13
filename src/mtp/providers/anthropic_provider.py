from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Iterator
import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..media import File, Image
from ..model_catalog import ANTHROPIC_DEFAULT_MODEL
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    STRUCTURED_OUTPUT_CLIENT_VALIDATED,
    STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA,
    USAGE_METRICS_RICH,
    calls_to_dependency_batches,
    extract_refs,
    extract_usage_metrics,
    normalize_refs,
    safe_load_arguments,
)


class AnthropicToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for Anthropic Claude.
    Sends tool definitions via the Anthropic Tool-Use API.
    """

    def __init__(
        self,
        *,
        model: str = ANTHROPIC_DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        strict_tools: bool = False,
        output_config: dict[str, Any] | None = None,
        thinking: dict[str, Any] | None = None,
        client: Any | None = None,
        async_client: Any | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.tool_choice = self._validate_tool_choice(tool_choice)
        self.strict_tools = strict_tools
        self.output_config = self._validate_output_config(output_config)
        self.thinking = dict(thinking) if thinking is not None else None
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._last_finalize_message: dict[str, Any] | None = None
        self._last_finalize_stop_reason: str | None = None
        self._last_finalize_stop_sequence: str | None = None
        self._last_stream_stop_reason: str | None = None
        self._last_stream_stop_sequence: str | None = None
        self._last_finalize_reasoning: list[dict[str, Any]] | None = None
        self._api_key = api_key
        self._client = client or self._make_client(api_key=api_key)
        self._async_client = async_client

    def _make_client(self, api_key: str | None) -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "`anthropic` not installed. Please install using `pip install anthropic`"
            ) from exc

        key = api_key or require_env("ANTHROPIC_API_KEY")
        return anthropic.Anthropic(api_key=key)

    def _get_async_client(self) -> Any:
        """Return the injected async client or lazily construct Anthropic's native one."""
        if self._async_client is not None:
            return self._async_client
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "`anthropic` not installed. Please install using `pip install anthropic`"
            ) from exc

        key = self._api_key or require_env("ANTHROPIC_API_KEY")
        self._async_client = anthropic.AsyncAnthropic(api_key=key)
        return self._async_client

    @staticmethod
    def _validate_tool_choice(value: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(value, str):
            choice_type = "any" if value == "required" else value
            if choice_type not in {"auto", "any", "none"}:
                raise ValueError(
                    "Anthropic tool_choice must be 'auto', 'any'/'required', 'none', "
                    "or {'type': 'tool', 'name': '...'}"
                )
            return {"type": choice_type}
        if not isinstance(value, dict):
            raise TypeError("Anthropic tool_choice must be a string or dictionary")
        choice = dict(value)
        choice_type = choice.get("type")
        if choice_type == "required":
            choice["type"] = choice_type = "any"
        if choice_type not in {"auto", "any", "none", "tool"}:
            raise ValueError("Invalid Anthropic tool_choice type")
        if choice_type == "tool" and not isinstance(choice.get("name"), str):
            raise ValueError("Anthropic tool_choice type 'tool' requires a tool name")
        return choice

    @staticmethod
    def _validate_output_config(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("Anthropic output_config must be a dictionary")
        output_config = dict(value)
        output_format = output_config.get("format")
        if not isinstance(output_format, dict):
            raise ValueError("Anthropic output_config requires a 'format' dictionary")
        if output_format.get("type") != "json_schema" or not isinstance(
            output_format.get("schema"), dict
        ):
            raise ValueError(
                "Anthropic output_config.format must contain type='json_schema' and a schema"
            )
        return output_config

    def _to_anthropic_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            definition: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema or {"type": "object", "properties": {}},
            }
            if self.strict_tools:
                definition["strict"] = True
            converted.append(definition)
        return converted

    def _request_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if self.output_config is not None:
            options["output_config"] = self.output_config
        if self.thinking is not None:
            options["thinking"] = self.thinking
        else:
            options["temperature"] = self.temperature
        return options

    def _to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, default=str)
        except Exception:
            return str(content)

    @staticmethod
    def _serialize_block(block: Any) -> dict[str, Any] | None:
        if isinstance(block, dict):
            return dict(block)
        model_dump = getattr(block, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(exclude_none=True)
            return dumped if isinstance(dumped, dict) else None
        values = getattr(block, "__dict__", None)
        if isinstance(values, dict):
            return {
                key: value
                for key, value in values.items()
                if not key.startswith("_") and value is not None
            }
        return None

    @staticmethod
    def _usage_metrics(response: Any) -> dict[str, int]:
        metrics = extract_usage_metrics(response)
        usage = (
            response.get("usage")
            if isinstance(response, dict)
            else getattr(response, "usage", None)
        )
        details = (
            usage.get("output_tokens_details")
            if isinstance(usage, dict)
            else getattr(usage, "output_tokens_details", None)
        )
        thinking_tokens = (
            details.get("thinking_tokens")
            if isinstance(details, dict)
            else getattr(details, "thinking_tokens", None)
        )
        if thinking_tokens is not None:
            try:
                metrics["reasoning_tokens"] = int(thinking_tokens)
            except (TypeError, ValueError):
                pass
        return metrics

    def _guess_mime(self, name_or_path: str, default: str) -> str:
        guessed = mimetypes.guess_type(name_or_path)[0]
        return guessed or default

    @staticmethod
    def _remote_url(url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Anthropic remote media URLs must be absolute HTTP(S) URLs")
        return url

    @staticmethod
    def _normalize_image_mime(mime: str) -> str:
        normalized = mime.lower().split(";", 1)[0].strip()
        if normalized == "image/jpg":
            normalized = "image/jpeg"
        allowed = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        if normalized not in allowed:
            raise ValueError(
                f"Unsupported Anthropic image media type {mime!r}; expected JPEG, PNG, GIF, or WebP"
            )
        return normalized

    @staticmethod
    def _validate_image_signature(raw: bytes, mime: str) -> None:
        signatures = {
            "image/jpeg": raw.startswith(b"\xff\xd8\xff"),
            "image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/gif": raw.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": len(raw) >= 12
            and raw.startswith(b"RIFF")
            and raw[8:12] == b"WEBP",
        }
        if not signatures[mime]:
            raise ValueError(
                f"Anthropic image content does not match declared media type {mime!r}"
            )

    def _image_block(self, image: Image) -> dict[str, Any] | None:
        if image.url:
            return {
                "type": "image",
                "source": {"type": "url", "url": self._remote_url(image.url)},
            }
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
        mime = self._normalize_image_mime(mime)
        self._validate_image_signature(raw, mime)
        encoded = base64.b64encode(raw).decode("utf-8")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": encoded},
        }

    def _file_block(self, file: File) -> dict[str, Any] | None:
        if file.url:
            return {
                "type": "document",
                "source": {"type": "url", "url": self._remote_url(file.url)},
                **(
                    {}
                    if self.output_config is not None
                    else {"citations": {"enabled": True}}
                ),
            }
        raw = file.get_content_bytes()
        if raw is None:
            return None
        file_name = file.filename
        if file_name is None and file.filepath is not None:
            file_name = Path(str(file.filepath)).name
        mime = file.mime_type or (
            self._guess_mime(file_name, "application/pdf")
            if file_name
            else "application/pdf"
        )
        normalized_mime = mime.lower().split(";", 1)[0].strip()
        if normalized_mime.startswith("text/") or normalized_mime == "application/json":
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Anthropic text documents must contain valid UTF-8") from exc
            return {
                "type": "document",
                "source": {
                    "type": "text",
                    "media_type": "text/plain",
                    "data": text,
                },
                **(
                    {}
                    if self.output_config is not None
                    else {"citations": {"enabled": True}}
                ),
            }
        if normalized_mime != "application/pdf":
            raise ValueError(
                f"Unsupported Anthropic document media type {mime!r}; expected PDF or UTF-8 text"
            )
        if b"%PDF-" not in raw[:1024]:
            raise ValueError("Anthropic PDF content does not have a valid PDF signature")
        encoded = base64.b64encode(raw).decode("utf-8")
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": mime, "data": encoded},
            **(
                {}
                if self.output_config is not None
                else {"citations": {"enabled": True}}
            ),
        }

    def _assistant_blocks(self, msg: dict[str, Any]) -> list[dict[str, Any]]:
        preserved = msg.get("anthropic_content")
        if isinstance(preserved, list) and all(isinstance(block, dict) for block in preserved):
            # Thinking and redacted-thinking signatures must be round-tripped byte-for-byte
            # when tool results continue an Anthropic conversation.
            return [dict(block) for block in preserved]
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

    def _to_anthropic_payload(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str | None, list[dict[str, Any]]]:
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
            **self._request_options(),
        }
        if tools and self.tool_choice != {"type": "auto"}:
            request["tool_choice"] = self.tool_choice
        if system_prompt:
            request["system"] = system_prompt

        response = self._client.messages.create(**request)
        return self._action_from_response(response)

    def _action_from_response(self, response: Any) -> AgentAction:
        """Parse one Messages API response identically for sync and async clients."""
        usage = self._usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "anthropic", "model": self.model}
        if usage:
            action_meta["usage"] = usage

        calls: list[ToolCall] = []
        serialized_tool_calls: list[dict[str, Any]] = []
        id_by_index: dict[int, str] = {}
        response_text_parts: list[str] = []
        reasoning_blocks: list[dict[str, Any]] = []
        preserved_blocks: list[dict[str, Any]] = []
        for idx, content in enumerate(response.content):
            serialized = self._serialize_block(content)
            if serialized is not None:
                preserved_blocks.append(serialized)
            if content.type == "text":
                text = getattr(content, "text", None)
                if isinstance(text, str) and text:
                    response_text_parts.append(text)
            elif content.type == "tool_use":
                call_id = content.id or f"call_{idx}"
                id_by_index[idx] = call_id
                raw_input = (
                    content.input
                    if isinstance(content.input, dict)
                    else dict(content.input)
                )
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
            elif content.type in {"thinking", "redacted_thinking"}:
                if serialized is not None:
                    reasoning_blocks.append(serialized)
        response_text = "\n".join(response_text_parts).strip()
        if reasoning_blocks:
            action_meta["reasoning"] = reasoning_blocks

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
                        "anthropic_content": preserved_blocks,
                        **({"reasoning": reasoning_blocks} if reasoning_blocks else {}),
                    },
                },
            )

        return AgentAction(response_text=response_text, metadata=action_meta)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        response = self._client.messages.create(**self._finalize_request(messages))
        self._last_finalize_usage = self._usage_metrics(response) or None
        self._remember_finalize_response(response)
        texts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
        if texts:
            return "\n".join(texts).strip()
        return "Done."

    def _finalize_request(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Build the common Messages API request used by sync and streaming finalization."""
        system_prompt, anthropic_messages = self._to_anthropic_payload(messages)
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
            **self._request_options(),
        }
        if system_prompt:
            request["system"] = system_prompt
        return request

    @staticmethod
    def _response_text(response: Any) -> str:
        content = getattr(response, "content", None)
        if not isinstance(content, list):
            return ""
        texts = [
            getattr(block, "text", "")
            for block in content
            if getattr(block, "type", None) == "text"
        ]
        return "\n".join(text for text in texts if isinstance(text, str)).strip()

    def _remember_finalize_response(self, response: Any, *, streamed: bool = False) -> None:
        """Retain terminal Messages API metadata without leaking SDK model objects."""
        stop_reason = getattr(response, "stop_reason", None)
        stop_sequence = getattr(response, "stop_sequence", None)
        normalized_reason = stop_reason if isinstance(stop_reason, str) else None
        normalized_sequence = stop_sequence if isinstance(stop_sequence, str) else None
        text = self._response_text(response)
        content = getattr(response, "content", None)
        reasoning: list[dict[str, Any]] = []
        if isinstance(content, list):
            for item in content:
                block = self._serialize_block(item)
                if block is not None and block.get("type") in {
                    "thinking",
                    "redacted_thinking",
                }:
                    reasoning.append(block)

        self._last_finalize_stop_reason = normalized_reason
        self._last_finalize_stop_sequence = normalized_sequence
        self._last_finalize_message = {"role": "assistant", "content": text or "Done."}
        self._last_finalize_reasoning = reasoning or None
        if streamed:
            self._last_stream_stop_reason = normalized_reason
            self._last_stream_stop_sequence = normalized_sequence

    def finalize_stream(
        self,
        messages: list[dict[str, Any]],
        tool_results: list[ToolResult],
    ) -> Iterator[str]:
        """Stream final text through Anthropic's native ``MessageStream`` helper.

        ``get_final_message`` is deliberately used after consuming
        ``text_stream``: the SDK's accumulated final message is the authoritative
        source for usage (including prompt-cache counters) and stop metadata.
        """
        del tool_results  # Results are already represented in ``messages``.
        self._last_stream_usage = None
        self._last_stream_stop_reason = None
        self._last_stream_stop_sequence = None
        self._last_finalize_message = None

        with self._client.messages.stream(**self._finalize_request(messages)) as stream:
            for text in stream.text_stream:
                if isinstance(text, str) and text:
                    yield text
            final_message = stream.get_final_message()

        usage = self._usage_metrics(final_message) or None
        self._last_stream_usage = usage
        # Keep non-stream and stream metadata consistent for consumers that
        # inspect the most recent finalization independent of transport mode.
        self._last_finalize_usage = usage
        self._remember_finalize_response(final_message, streamed=True)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="anthropic",
            supports_tool_calling=True,
            supports_parallel_tool_calls=True,
            input_modalities=["text", "image", "file"],
            supports_tool_media_output=True,
            supports_finalize_streaming=True,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=True,
            structured_output_support=(
                STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA
                if self.output_config is not None
                else STRUCTURED_OUTPUT_CLIENT_VALIDATED
            ),
            supports_native_async=True,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        system_prompt, anthropic_messages = self._to_anthropic_payload(messages)
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
            "tools": self._to_anthropic_tools(tools) if tools else [],
            **self._request_options(),
        }
        if tools and self.tool_choice != {"type": "auto"}:
            request["tool_choice"] = self.tool_choice
        if system_prompt:
            request["system"] = system_prompt
        response = await self._get_async_client().messages.create(**request)
        return self._action_from_response(response)

    async def afinalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        del tool_results
        response = await self._get_async_client().messages.create(
            **self._finalize_request(messages)
        )
        self._last_finalize_usage = self._usage_metrics(response) or None
        self._remember_finalize_response(response)
        return self._response_text(response) or "Done."

    async def afinalize_stream(
        self,
        messages: list[dict[str, Any]],
        tool_results: list[ToolResult],
    ) -> AsyncIterator[str]:
        """Stream final text using Anthropic's native async message stream."""
        del tool_results
        self._last_stream_usage = None
        self._last_stream_stop_reason = None
        self._last_stream_stop_sequence = None
        self._last_finalize_message = None

        async with self._get_async_client().messages.stream(
            **self._finalize_request(messages)
        ) as stream:
            async for text in stream.text_stream:
                if isinstance(text, str) and text:
                    yield text
            final_message = await stream.get_final_message()

        usage = self._usage_metrics(final_message) or None
        self._last_stream_usage = usage
        self._last_finalize_usage = usage
        self._remember_finalize_response(final_message, streamed=True)
