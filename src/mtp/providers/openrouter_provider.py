from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..media import Audio, File, Image, Video
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import (
    calls_to_dependency_batches,
    extract_refs,
    extract_usage_metrics,
    normalize_refs,
    safe_load_arguments,
)


class OpenRouterToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for OpenRouter.
    Uses OpenAI-compatible tool calling.
    """

    def __init__(
        self,
        *,
        model: str = "qwen/qwen3.6-plus-preview:free",
        api_key: str | None = None,
        site_url: str | None = None,
        site_name: str | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.site_url = site_url
        self.site_name = site_name
        self._last_finalize_usage: dict[str, int] | None = None
        self._client = client or self._make_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. For OpenRouter, install dependencies with "
                "`pip install openai` and `pip install openrouter`."
            ) from exc

        key = api_key or require_env("OPENROUTER_API_KEY")
        
        # OpenRouter specific headers
        extra_headers = {}
        if self.site_url:
            extra_headers["HTTP-Referer"] = self.site_url
        if self.site_name:
            extra_headers["X-Title"] = self.site_name

        return OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
            default_headers=extra_headers,
            timeout=60.0,
        )

    def _to_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            import json

            return json.dumps(value, default=str)
        except Exception:
            return str(value)

    def _guess_mime(self, name_or_path: str, default: str) -> str:
        guessed = mimetypes.guess_type(name_or_path)[0]
        return guessed or default

    def _to_data_url(self, payload: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(payload).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    def _image_part(self, image: Image) -> dict[str, Any] | None:
        if image.url:
            part: dict[str, Any] = {"type": "image_url", "image_url": {"url": image.url}}
            if image.detail:
                part["image_url"]["detail"] = image.detail
            return part
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
        data_url = self._to_data_url(raw, mime)
        part = {"type": "image_url", "image_url": {"url": data_url}}
        if image.detail:
            part["image_url"]["detail"] = image.detail
        return part

    def _audio_part(self, audio: Audio) -> dict[str, Any] | None:
        raw = audio.get_content_bytes()
        if raw is None:
            return None
        audio_format = audio.format
        if audio_format is None:
            if audio.filepath:
                audio_format = Path(str(audio.filepath)).suffix.lstrip(".") or "wav"
            elif audio.mime_type and "/" in audio.mime_type:
                audio_format = audio.mime_type.split("/", 1)[1]
            else:
                audio_format = "wav"
        encoded = base64.b64encode(raw).decode("utf-8")
        return {"type": "input_audio", "input_audio": {"data": encoded, "format": audio_format}}

    def _file_part(self, file: File) -> dict[str, Any] | None:
        filename = file.filename
        if filename is None and file.filepath is not None:
            filename = Path(str(file.filepath)).name
        if filename is None and file.url:
            filename = Path(file.url).name or "file"
        if filename is None:
            filename = "file"

        if file.url:
            mime = file.mime_type or self._guess_mime(file.url, "application/octet-stream")
            if mime.startswith("text/") or mime == "application/json":
                return {"type": "text", "text": f"[file:{filename}] {file.url}"}
            return {
                "type": "file",
                "file": {
                    "filename": filename,
                    "file_data": file.url,
                },
            }

        raw = file.get_content_bytes()
        if raw is None:
            return None
        mime = file.mime_type or self._guess_mime(filename, "application/octet-stream")
        if mime.startswith("text/") or mime == "application/json":
            text_content = raw.decode("utf-8", errors="replace")
            return {"type": "text", "text": f"[file:{filename}] {text_content}"}
        data_url = self._to_data_url(raw, mime)
        return {"type": "file", "file": {"filename": filename, "file_data": data_url}}

    def _video_part(self, video: Video) -> dict[str, Any] | None:
        if video.url:
            return {"type": "video_url", "video_url": {"url": video.url}}
        raw = video.get_content_bytes()
        if raw is None:
            return None
        mime = video.mime_type
        if mime is None:
            if video.format:
                mime = f"video/{video.format}"
            elif video.filepath:
                mime = self._guess_mime(str(video.filepath), "video/mp4")
            else:
                mime = "video/mp4"
        data_url = self._to_data_url(raw, mime)
        return {"type": "video_url", "video_url": {"url": data_url}}

    def _to_openrouter_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                continue

            if role == "tool":
                formatted.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.get("tool_call_id"),
                        "content": self._to_text(msg.get("content", "")),
                    }
                )
                continue

            converted: dict[str, Any] = {"role": role}
            if role == "assistant" and "tool_calls" in msg:
                converted["tool_calls"] = msg["tool_calls"]

            text = self._to_text(msg.get("content", ""))
            parts: list[dict[str, Any]] = [{"type": "text", "text": text}]

            if role == "user":
                images = msg.get("images")
                if isinstance(images, list):
                    for image in images:
                        if isinstance(image, Image):
                            part = self._image_part(image)
                            if part is not None:
                                parts.append(part)

                audios = msg.get("audios")
                if audios is None:
                    audios = msg.get("audio")
                if isinstance(audios, list):
                    for audio in audios:
                        if isinstance(audio, Audio):
                            part = self._audio_part(audio)
                            if part is not None:
                                parts.append(part)

                files = msg.get("files")
                if isinstance(files, list):
                    for file in files:
                        if isinstance(file, File):
                            part = self._file_part(file)
                            if part is not None:
                                parts.append(part)

                videos = msg.get("videos")
                if isinstance(videos, list):
                    for video in videos:
                        if isinstance(video, Video):
                            part = self._video_part(video)
                            if part is not None:
                                parts.append(part)

            converted["content"] = parts if parts else text
            formatted.append(converted)
        return formatted

    def _to_openai_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
        ]

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        openrouter_messages = self._to_openrouter_messages(messages)
        openai_tools = self._to_openai_tools(tools)

        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": openrouter_messages,
            "temperature": self.temperature,
        }
        if openai_tools:
            request_args["tools"] = openai_tools
            request_args["tool_choice"] = self.tool_choice

        response = self._client.chat.completions.create(**request_args)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "openrouter", "model": self.model}
        if usage:
            action_meta["usage"] = usage

        if tool_calls:
            mtp_calls: list[ToolCall] = []
            id_by_index: dict[int, str] = {}
            serialized_tool_calls: list[dict[str, Any]] = []
            for idx, tc in enumerate(tool_calls):
                call_id = tc.id or f"call_{idx}"
                id_by_index[idx] = call_id
                parsed_args = safe_load_arguments(tc.function.arguments)
                normalized_args = normalize_refs(parsed_args, id_by_index)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                mtp_calls.append(
                    ToolCall(
                        id=call_id,
                        name=tc.function.name,
                        arguments=normalized_args,
                        depends_on=depends_on,
                    )
                )
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                    }
                )

            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "openrouter", "model": self.model}
            )
            
            return AgentAction(
                plan=plan,
                metadata={
                    **action_meta,
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": serialized_tool_calls,
                    },
                },
            )

        return AgentAction(response_text=message.content or "", metadata=action_meta)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        openrouter_messages = self._to_openrouter_messages(messages)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=openrouter_messages,
            temperature=self.temperature,
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
