"""Stateless Responses API with native output-item replay through saved sessions."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import mimetypes
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..agent import AgentAction
from ..async_stream import async_from_sync
from ..config import require_env
from ..media import File, Image
from ..protocol import ToolSpec
from .common import extract_usage_metrics
from .compatible_provider import OpenAICompatibleToolCallingProvider, read_value


def _validate_media_url(url: str, *, allow_data: bool = False) -> None:
    if not isinstance(url, str) or any(
        char.isspace() or unicodedata.category(char) in {"Cc", "Cf"} for char in url
    ):
        raise ValueError("Media URL contains whitespace or controls.")
    if allow_data and url.startswith("data:image/") and ";base64," in url:
        return
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Media URL contains an invalid host or port.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or "@" in parsed.netloc
        or "%" in parsed.netloc
        or "\\" in url
        or port == 0
    ):
        raise ValueError("Media URL must be HTTP(S) without credentials.")


def output_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return copy.deepcopy(item)
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    raise TypeError("Responses output items must be SDK models or dictionaries.")


class ResponseStreamAccumulator:
    def __init__(self):
        self.completed = None

    def feed(self, event):
        kind = read_value(event, "type")
        if self.completed is not None and kind in {
            "response.completed",
            "response.output_text.delta",
            "response.reasoning_summary_text.delta",
            "response.function_call_arguments.delta",
            "response.output_item.added",
            "response.output_item.done",
        }:
            raise ValueError("Responses stream returned output after completion.")
        if kind == "response.output_text.delta":
            yield {
                "type": "text_chunk",
                "chunk": read_value(event, "delta", ""),
            }
        elif kind == "response.reasoning_summary_text.delta":
            yield {
                "type": "reasoning_chunk",
                "chunk": read_value(event, "delta", ""),
            }
        elif kind == "response.completed":
            self.completed = read_value(event, "response")
        elif kind in {"error", "response.failed", "response.incomplete"}:
            raise ValueError(f"Responses stream failed with event {kind}.")

    def action(self, provider):
        if self.completed is None:
            raise ValueError("Responses stream ended without its completed response.")
        return provider._response_action(self.completed)


class OpenAIResponsesToolCallingProvider(OpenAICompatibleToolCallingProvider):
    _supported_input_modalities = frozenset({"text", "image", "file"})

    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        max_output_tokens: int | None = 1024,
        reasoning_effort: str | None = None,
        input_modalities: tuple[str, ...] = ("text",),
        enable_multimodal: bool = False,
        provider_name: str = "openai_responses",
        **kwargs: Any,
    ) -> None:
        if "max_tokens" in kwargs:
            raise ValueError("Responses uses max_output_tokens; remove max_tokens.")
        if reasoning_effort is not None and (
            not isinstance(reasoning_effort, str) or not reasoning_effort.strip()
        ):
            raise ValueError("reasoning_effort must be a nonempty string or None.")
        if type(enable_multimodal) is not bool:
            raise TypeError("enable_multimodal must be a boolean.")
        if not enable_multimodal and (
            not isinstance(input_modalities, (tuple, list))
            or tuple(input_modalities) != ("text",)
        ):
            raise ValueError("Responses currently supports text input only.")
        extra_body = kwargs.get("extra_body")
        if extra_body is not None and not isinstance(extra_body, dict):
            raise TypeError("extra_body must be a dictionary or None.")
        if {
            "store",
            "include",
            "previous_response_id",
            "max_output_tokens",
            "reasoning",
            "conversation",
            "background",
            "text",
        }.intersection(extra_body or {}):
            raise ValueError(
                "Responses extra_body cannot override state, reasoning or token settings."
            )
        key = api_key or (
            require_env("OPENAI_API_KEY")
            if kwargs.get("client") is None and kwargs.get("async_client") is None
            else None
        )
        self.reasoning_effort = reasoning_effort
        super().__init__(
            model=model,
            api_key=key,
            base_url=base_url,
            provider_name=provider_name,
            max_tokens=max_output_tokens,
            input_modalities=input_modalities,
            **kwargs,
        )

    def _input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = []
        for message in messages:
            if (
                any(message.get(field) for field in ("audios", "audio", "videos"))
                or (message.get("images") and "image" not in self.input_modalities)
                or (message.get("files") and "file" not in self.input_modalities)
            ):
                raise ValueError("Responses currently supports text input only.")
            content = message.get("content", "")
            if (
                message.get("role") != "tool"
                and isinstance(content, list)
                and any(
                    isinstance(part, dict)
                    and part.get("type")
                    in {
                        *(
                            set()
                            if "image" in self.input_modalities
                            else {"image", "image_url", "input_image"}
                        ),
                        "audio",
                        "input_audio",
                        "video",
                        *(
                            set()
                            if "file" in self.input_modalities
                            else {"file", "input_file"}
                        ),
                    }
                    for part in content
                )
            ):
                raise ValueError("Responses currently supports text input only.")
            native = message.get("responses_items")
            if message.get("role") == "assistant" and isinstance(native, list):
                items.extend(copy.deepcopy(native))
                continue
            role = message.get("role")
            if role == "tool":
                value = message.get("content", "")
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message["tool_call_id"],
                        "output": value
                        if isinstance(value, str)
                        else json.dumps(value, default=str),
                    }
                )
                continue
            if role not in {"system", "user", "assistant", "developer"}:
                continue
            content = message.get("content", "")
            if message.get("images") or message.get("files"):
                parts = [
                    {
                        "type": "input_text",
                        "text": content
                        if isinstance(content, str)
                        else json.dumps(content, default=str),
                    }
                ]
                parts.extend(
                    self._image_input(image) for image in message.get("images") or []
                )
                parts.extend(
                    self._file_input(file) for file in message.get("files") or []
                )
                content = parts
            elif isinstance(content, list) and self.input_modalities != ("text",):
                for part in content:
                    if not isinstance(part, dict) or part.get("type") not in {
                        "input_text",
                        "input_image",
                        "input_file",
                    }:
                        raise ValueError(
                            "Responses content blocks must use native input types."
                        )
                    if part["type"] == "input_image" and part.get("image_url"):
                        _validate_media_url(part["image_url"], allow_data=True)
                    if part["type"] == "input_file" and part.get("file_url"):
                        _validate_media_url(part["file_url"])
                content = copy.deepcopy(content)
            elif not isinstance(content, str):
                content = json.dumps(content, default=str)
            if content or not message.get("tool_calls"):
                items.append({"role": role, "content": content})
            for call in message.get("tool_calls") or []:
                function = call["function"]
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": self._tool_names.wire_name(function["name"]),
                        "arguments": function["arguments"],
                    }
                )
        return items

    @staticmethod
    def _image_input(image: Image) -> dict[str, Any]:
        if not isinstance(image, Image):
            raise TypeError("Responses images must be Image objects.")
        if image.url:
            _validate_media_url(image.url, allow_data=True)
            url = image.url
        else:
            raw = image.get_content_bytes()
            if raw is None or len(raw) > 20 * 1024 * 1024:
                raise ValueError("Image data is missing or exceeds 20 MiB.")
            mime = (
                image.mime_type
                or (
                    f"image/{image.format}"
                    if image.format
                    else mimetypes.guess_type(str(image.filepath or ""))[0]
                )
                or "image/jpeg"
            )
            url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        part = {"type": "input_image", "image_url": url}
        if image.detail:
            if image.detail not in {"auto", "low", "high", "original"}:
                raise ValueError("Unsupported image detail.")
            part["detail"] = image.detail
        return part

    @staticmethod
    def _file_input(file: File) -> dict[str, Any]:
        if not isinstance(file, File):
            raise TypeError("Responses files must be File objects.")
        if file.id:
            return {"type": "input_file", "file_id": file.id}
        if file.url:
            _validate_media_url(file.url)
            return {"type": "input_file", "file_url": file.url}
        raw = file.get_content_bytes()
        if raw is None or len(raw) > 20 * 1024 * 1024:
            raise ValueError("File data is missing or exceeds 20 MiB.")
        filename = file.filename or Path(str(file.filepath or "file")).name
        mime = (
            file.mime_type
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        return {
            "type": "input_file",
            "filename": filename,
            "file_data": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
        }

    def _request(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "input": self._input(messages),
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if self.max_tokens is not None:
            request["max_output_tokens"] = self.max_tokens
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.reasoning_effort is not None:
            request["reasoning"] = {"effort": self.reasoning_effort}
        if self.extra_body:
            request["extra_body"] = copy.deepcopy(self.extra_body)
        if self.output_schema is not None:
            request["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "mtp_output",
                    "strict": True,
                    "schema": copy.deepcopy(self.output_schema),
                }
            }
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "name": self._tool_names.wire_name(tool.name),
                    "description": tool.description,
                    "parameters": tool.input_schema
                    or {"type": "object", "properties": {}},
                    "strict": False,
                }
                for tool in tools
            ]
            choice = copy.deepcopy(self.tool_choice)
            if isinstance(choice, dict) and choice.get("type") == "function":
                function = choice.get("function", choice)
                choice = {
                    "type": "function",
                    "name": self._tool_names.wire_name(function["name"]),
                }
            request["tool_choice"] = choice
            if self.parallel_tool_calls is not None:
                request["parallel_tool_calls"] = self.parallel_tool_calls
        if stream:
            request["stream"] = True
        return request

    def _response_action(self, response: Any) -> AgentAction:
        status = read_value(response, "status")
        if status != "completed":
            raise ValueError(f"Responses request did not complete, status={status!r}.")
        raw_items = read_value(response, "output")
        if not isinstance(raw_items, list):
            raise TypeError("Responses request is missing output items.")
        native = [output_item(item) for item in raw_items]
        calls = []
        text = []
        for item in native:
            kind = item.get("type")
            if kind == "function_call":
                if item.get("status") not in {None, "completed"}:
                    raise ValueError("Responses returned an unfinished function call.")
                calls.append(
                    {
                        "id": item.get("call_id"),
                        "function": {
                            "name": item.get("name"),
                            "arguments": item.get("arguments"),
                        },
                    }
                )
            elif kind == "message":
                if item.get("status") not in {None, "completed"}:
                    raise ValueError("Responses returned an unfinished message.")
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        text.append(part.get("text", ""))
                    elif part.get("type") == "refusal":
                        text.append(part.get("refusal", ""))
            elif kind != "reasoning":
                raise ValueError(f"Unsupported Responses output item: {kind!r}")
        if not calls and not text:
            raise ValueError(
                "Responses returned no text, refusal, or executable calls."
            )
        action = self._action(
            "".join(text), calls, None, extract_usage_metrics(response)
        )
        assistant = action.metadata.get(
            "assistant_tool_message", action.metadata["assistant_message"]
        )
        assistant["responses_items"] = native
        action.metadata["assistant_message"] = assistant
        return action

    def next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        return self._response_action(
            self._client.responses.create(**self._request(messages, tools))
        )

    def stream_next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> Iterator[AgentAction | dict[str, Any]]:
        stream = self._client.responses.create(
            **self._request(messages, tools, stream=True)
        )
        state = ResponseStreamAccumulator()
        try:
            for event in stream:
                yield from state.feed(event)
            yield state.action(self)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    async def anext_action(self, messages, tools):
        if self._async_client is None:
            return await asyncio.to_thread(self.next_action, messages, tools)
        response = await self._async_client.responses.create(
            **self._request(messages, tools)
        )
        return self._response_action(response)

    async def astream_next_action(self, messages, tools):
        if self._async_client is None:
            async for item in async_from_sync(
                lambda: self.stream_next_action(messages, tools)
            ):
                yield item
            return
        stream = await self._async_client.responses.create(
            **self._request(messages, tools, stream=True)
        )
        state = ResponseStreamAccumulator()
        try:
            async for event in stream:
                for item in state.feed(event):
                    yield item
            yield state.action(self)
        finally:
            await stream.close()

    def capabilities(self):
        result = super().capabilities()
        result.input_modalities = list(self.input_modalities)
        return result
