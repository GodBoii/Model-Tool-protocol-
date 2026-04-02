from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..media import Audio, File, Image, Video
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import calls_to_dependency_batches, extract_refs, extract_usage_metrics, normalize_refs, safe_load_arguments


class GeminiToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for Google Gemini.
    Uses the modern google.genai SDK.
    """

    def __init__(
        self,
        *,
        model: str = "gemini-2.0-flash",
        api_key: str | None = None,
        temperature: float = 0.0,
        client: Any | None = None,
    ) -> None:
        self.model_name = model
        self.temperature = temperature
        self._last_finalize_usage: dict[str, int] | None = None
        self._client = client or self._make_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "`google-genai` not installed. Please install using `pip install google-genai`"
            ) from exc

        key = api_key or require_env("GEMINI_API_KEY")
        return genai.Client(api_key=key)

    def _to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, default=str)
        except Exception:
            return str(content)

    def _get_content_and_part_types(self) -> tuple[Any, Any]:
        try:
            from google.genai.types import Content, Part

            return Content, Part
        except Exception:
            class _Part:
                def __init__(self, *, text: str | None = None, function_call: Any = None, function_response: Any = None) -> None:
                    self.text = text
                    self.function_call = function_call
                    self.function_response = function_response

                @staticmethod
                def from_text(text: str) -> "_Part":
                    return _Part(text=text)

                @staticmethod
                def from_bytes(*, mime_type: str, data: bytes) -> "_Part":
                    return _Part(text=f"[bytes:{mime_type}:{len(data)}]")

                @staticmethod
                def from_uri(*, file_uri: str, mime_type: str) -> "_Part":
                    return _Part(text=f"[uri:{mime_type}:{file_uri}]")

                @staticmethod
                def from_function_call(*, name: str, args: dict[str, Any]) -> "_Part":
                    return _Part(function_call=SimpleNamespace(name=name, args=args))

                @staticmethod
                def from_function_response(*, name: str, response: dict[str, Any]) -> "_Part":
                    return _Part(function_response=SimpleNamespace(name=name, response=response))

            class _Content:
                def __init__(self, *, role: str, parts: list[Any]) -> None:
                    self.role = role
                    self.parts = parts

            return _Content, _Part

    def _guess_mime(self, name_or_path: str, default: str) -> str:
        guessed = mimetypes.guess_type(name_or_path)[0]
        return guessed or default

    def _image_part(self, image: Image, *, Part: Any) -> Any | None:
        if image.url:
            mime = image.mime_type or "image/jpeg"
            return Part.from_uri(file_uri=image.url, mime_type=mime)
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
        return Part.from_bytes(mime_type=mime, data=raw)

    def _audio_part(self, audio: Audio, *, Part: Any) -> Any | None:
        if audio.url:
            mime = audio.mime_type
            if mime is None:
                if audio.format:
                    mime = f"audio/{audio.format}"
                else:
                    mime = "audio/mpeg"
            return Part.from_uri(file_uri=audio.url, mime_type=mime)
        raw = audio.get_content_bytes()
        if raw is None:
            return None
        mime = audio.mime_type
        if mime is None:
            if audio.format:
                mime = f"audio/{audio.format}"
            elif audio.filepath:
                mime = self._guess_mime(str(audio.filepath), "audio/mpeg")
            else:
                mime = "audio/mpeg"
        return Part.from_bytes(mime_type=mime, data=raw)

    def _video_part(self, video: Video, *, Part: Any) -> Any | None:
        if video.url:
            mime = video.mime_type
            if mime is None:
                if video.format:
                    mime = f"video/{video.format}"
                else:
                    mime = "video/mp4"
            return Part.from_uri(file_uri=video.url, mime_type=mime)
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
        return Part.from_bytes(mime_type=mime, data=raw)

    def _file_part(self, file: File, *, Part: Any) -> Any | None:
        if file.url:
            mime = file.mime_type or self._guess_mime(file.url, "application/octet-stream")
            return Part.from_uri(file_uri=file.url, mime_type=mime)
        raw = file.get_content_bytes()
        if raw is None:
            return None
        file_name = file.filename
        if file_name is None and file.filepath is not None:
            file_name = Path(str(file.filepath)).name
        mime = file.mime_type or (self._guess_mime(file_name, "application/octet-stream") if file_name else "application/octet-stream")
        return Part.from_bytes(mime_type=mime, data=raw)

    def _to_gemini_payload(self, messages: list[dict[str, Any]]) -> tuple[list[Any], str | None]:
        Content, Part = self._get_content_and_part_types()

        contents: list[Any] = []
        system_lines: list[str] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                text = self._to_text(msg.get("content", ""))
                if text.strip():
                    system_lines.append(text)
                continue

            parts: list[Any] = []
            text = self._to_text(msg.get("content", ""))
            if text.strip():
                parts.append(Part.from_text(text=text))

            if role == "user":
                images = msg.get("images")
                if isinstance(images, list):
                    for image in images:
                        if isinstance(image, Image):
                            image_part = self._image_part(image, Part=Part)
                            if image_part is not None:
                                parts.append(image_part)
                audios = msg.get("audios")
                if audios is None:
                    audios = msg.get("audio")
                if isinstance(audios, list):
                    for audio in audios:
                        if isinstance(audio, Audio):
                            audio_part = self._audio_part(audio, Part=Part)
                            if audio_part is not None:
                                parts.append(audio_part)
                videos = msg.get("videos")
                if isinstance(videos, list):
                    for video in videos:
                        if isinstance(video, Video):
                            video_part = self._video_part(video, Part=Part)
                            if video_part is not None:
                                parts.append(video_part)
                files = msg.get("files")
                if isinstance(files, list):
                    for file in files:
                        if isinstance(file, File):
                            file_part = self._file_part(file, Part=Part)
                            if file_part is not None:
                                parts.append(file_part)
                if parts:
                    contents.append(Content(role="user", parts=parts))
                continue

            if role == "assistant":
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
                            args = safe_load_arguments(raw_arguments)
                        elif isinstance(raw_arguments, dict):
                            args = raw_arguments
                        else:
                            args = {}
                        parts.append(Part.from_function_call(name=name, args=args))
                if parts:
                    contents.append(Content(role="model", parts=parts))
                continue

            if role == "tool":
                tool_name = msg.get("tool_name")
                if not isinstance(tool_name, str) or not tool_name:
                    tool_name = "tool"
                tool_content = msg.get("content")
                if isinstance(tool_content, (dict, list, str, int, float, bool)) or tool_content is None:
                    result_payload = tool_content
                else:
                    result_payload = self._to_text(tool_content)
                parts.append(Part.from_function_response(name=tool_name, response={"result": result_payload}))
                contents.append(Content(role="user", parts=parts))
                continue

            if parts:
                contents.append(Content(role="user", parts=parts))

        merged: list[Any] = []
        for content in contents:
            if merged and merged[-1].role == content.role:
                merged[-1].parts.extend(content.parts)
            else:
                merged.append(content)
        system_instruction = "\n\n".join(system_lines).strip() or None
        return merged, system_instruction

    def _extract_response_text(self, response: Any) -> str:
        direct_text = getattr(response, "text", None)
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text.strip()
        texts: list[str] = []
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text:
                    texts.append(part_text)
        return "\n".join(texts).strip()

    def _is_ref_schema(self, schema: dict[str, Any]) -> bool:
        props = schema.get("properties")
        required = schema.get("required")
        return (
            schema.get("type") == "object"
            and isinstance(props, dict)
            and "$ref" in props
            and isinstance(required, list)
            and "$ref" in required
        )

    def _sanitize_schema_for_gemini(self, schema: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = {"type", "properties", "required", "items", "description", "enum", "nullable"}
        sanitized: dict[str, Any] = {}

        for key, value in schema.items():
            if key not in allowed_keys:
                continue
            if key == "properties" and isinstance(value, dict):
                props: dict[str, Any] = {}
                for prop_name, prop_schema in value.items():
                    if isinstance(prop_schema, dict):
                        props[prop_name] = self._sanitize_schema_for_gemini(prop_schema)
                sanitized["properties"] = props
            elif key == "items" and isinstance(value, dict):
                sanitized["items"] = self._sanitize_schema_for_gemini(value)
            else:
                sanitized[key] = value

        any_of = schema.get("anyOf")
        if isinstance(any_of, list) and any_of:
            non_ref_options = [
                option
                for option in any_of
                if isinstance(option, dict) and not self._is_ref_schema(option)
            ]
            chosen = non_ref_options[0] if non_ref_options else next(
                (option for option in any_of if isinstance(option, dict)),
                None,
            )
            if isinstance(chosen, dict):
                return self._sanitize_schema_for_gemini(chosen)

        if "type" not in sanitized:
            sanitized["type"] = "object"

        return sanitized

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        contents, system_instruction = self._to_gemini_payload(messages)

        genai_tools: list[dict[str, Any]] = []
        if tools:
            functions = []
            for tool in tools:
                functions.append({
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": self._sanitize_schema_for_gemini(
                        tool.input_schema or {"type": "object", "properties": {}}
                    ),
                })
            genai_tools = [{"function_declarations": functions}]

        config: dict[str, Any] = {"temperature": self.temperature}
        if system_instruction:
            config["system_instruction"] = system_instruction
        if genai_tools:
            config["tools"] = genai_tools

        response = self._client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "gemini", "model": self.model_name}
        if usage:
            action_meta["usage"] = usage

        calls: list[ToolCall] = []
        serialized_tool_calls: list[dict[str, Any]] = []
        id_by_index: dict[int, str] = {}
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            parts = getattr(candidates[0].content, "parts", None) or []
            for idx, part in enumerate(parts):
                fn = getattr(part, "function_call", None)
                if fn:
                    call_id = f"gemini_call_{idx}"
                    id_by_index[idx] = call_id
                    raw_args = fn.args if isinstance(fn.args, dict) else dict(fn.args)
                    normalized_args = normalize_refs(raw_args, id_by_index)
                    depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                    calls.append(
                        ToolCall(
                            id=call_id,
                            name=fn.name,
                            arguments=normalized_args,
                            depends_on=depends_on,
                        )
                    )
                    serialized_tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": fn.name, "arguments": json.dumps(raw_args, default=str)},
                        }
                    )

        response_text = self._extract_response_text(response)
        if calls:
            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(calls),
                metadata={"provider": "gemini", "model": self.model_name}
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
        contents, system_instruction = self._to_gemini_payload(messages)
        config: dict[str, Any] = {"temperature": self.temperature}
        if system_instruction:
            config["system_instruction"] = system_instruction
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        text = self._extract_response_text(response)
        return text or "Done."

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
