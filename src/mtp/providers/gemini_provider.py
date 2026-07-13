from __future__ import annotations

import base64
import inspect
import json
import mimetypes
from pathlib import Path
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..media import Audio, File, Image, Video
from ..model_catalog import GEMINI_DEFAULT_MODEL
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


class GeminiToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for Google Gemini.
    Uses the modern google.genai SDK.
    """

    def __init__(
        self,
        *,
        model: str = GEMINI_DEFAULT_MODEL,
        api_key: str | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        response_schema: Any | None = None,
        response_json_schema: dict[str, Any] | None = None,
        response_mime_type: str | None = None,
        client: Any | None = None,
        async_client: Any | None = None,
    ) -> None:
        self.model_name = model
        self.temperature = temperature
        self.tool_choice = tool_choice
        if response_schema is not None and response_json_schema is not None:
            raise ValueError("Pass only one of response_schema or response_json_schema")
        self.response_schema = response_schema
        self.response_json_schema = response_json_schema
        self.response_mime_type = response_mime_type
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._last_finalize_reasoning: str | None = None
        self._last_stream_reasoning: str | None = None
        self._api_key = api_key
        self._client = client or self._make_client(api_key=api_key)
        self._async_client = async_client

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "`google-genai` not installed. Please install using `pip install google-genai`"
            ) from exc

        key = api_key or require_env("GEMINI_API_KEY")
        return genai.Client(api_key=key)

    def _get_async_client(self) -> Any:
        """Return google-genai's native asynchronous client.

        ``google.genai.Client`` exposes its async API through ``client.aio``.
        Accepting an explicit client keeps the adapter straightforward to test
        and supports applications that manage the async client's lifecycle.
        """
        if self._async_client is not None:
            return self._async_client
        aio = getattr(self._client, "aio", None)
        if aio is not None:
            self._async_client = aio
            return aio
        raise RuntimeError(
            "The configured Gemini client does not expose the native async API; "
            "pass async_client=... or use google.genai.Client."
        )

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
                def __init__(
                    self,
                    *,
                    text: str | None = None,
                    function_call: Any = None,
                    function_response: Any = None,
                    thought: bool | None = None,
                    thought_signature: bytes | None = None,
                ) -> None:
                    self.text = text
                    self.function_call = function_call
                    self.function_response = function_response
                    self.thought = thought
                    self.thought_signature = thought_signature

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
                    return _Part(function_call=SimpleNamespace(name=name, args=args, id=None))

                @staticmethod
                def from_function_response(*, name: str, response: dict[str, Any]) -> "_Part":
                    return _Part(
                        function_response=SimpleNamespace(name=name, response=response, id=None)
                    )

            class _Content:
                def __init__(self, *, role: str, parts: list[Any]) -> None:
                    self.role = role
                    self.parts = parts

            return _Content, _Part

    def _serialize_model_parts(self, parts: list[Any]) -> list[dict[str, Any]]:
        """Keep Gemini's model parts intact enough for a subsequent tool turn.

        Gemini 3 requires the opaque thought signature on the same function-call
        part where it was returned.  MTP histories must be JSON serializable, so
        signatures are stored as base64 and restored to bytes at request time.
        """
        serialized: list[dict[str, Any]] = []
        for part in parts:
            item: dict[str, Any] = {}
            text = getattr(part, "text", None)
            if isinstance(text, str):
                item["text"] = text
            function_call = getattr(part, "function_call", None)
            if function_call is not None:
                name = getattr(function_call, "name", None)
                raw_args = getattr(function_call, "args", None)
                if isinstance(name, str) and name:
                    try:
                        args = raw_args if isinstance(raw_args, dict) else dict(raw_args or {})
                    except (TypeError, ValueError):
                        args = {}
                    item["function_call"] = {"name": name, "args": args}
                    call_id = getattr(function_call, "id", None)
                    if isinstance(call_id, str) and call_id:
                        item["function_call"]["id"] = call_id
            thought = getattr(part, "thought", None)
            if isinstance(thought, bool):
                item["thought"] = thought
            signature = getattr(part, "thought_signature", None)
            if isinstance(signature, bytes):
                item["thought_signature"] = base64.b64encode(signature).decode("ascii")
            elif isinstance(signature, str) and signature:
                # Accommodate lightweight test doubles and future SDK shapes.
                item["thought_signature"] = signature
            if item:
                serialized.append(item)
        return serialized

    def _restore_model_part(self, item: dict[str, Any], *, Part: Any) -> Any | None:
        function_call = item.get("function_call")
        if isinstance(function_call, dict):
            name = function_call.get("name")
            args = function_call.get("args")
            if not isinstance(name, str) or not name:
                return None
            part = Part.from_function_call(
                name=name,
                args=args if isinstance(args, dict) else {},
            )
            call_id = function_call.get("id")
            restored_call = getattr(part, "function_call", None)
            if isinstance(call_id, str) and call_id and restored_call is not None:
                setattr(restored_call, "id", call_id)
        elif isinstance(item.get("text"), str):
            part = Part.from_text(text=item["text"])
        elif "thought" in item or "thought_signature" in item:
            part = Part()
        else:
            return None

        thought = item.get("thought")
        if isinstance(thought, bool):
            setattr(part, "thought", thought)
        encoded_signature = item.get("thought_signature")
        if isinstance(encoded_signature, str) and encoded_signature:
            try:
                signature = base64.b64decode(encoded_signature, validate=True)
            except (ValueError, TypeError):
                # A raw string is not expected from google-genai, but retaining it
                # is preferable to silently dropping an SDK-compatible value.
                signature = encoded_signature
            setattr(part, "thought_signature", signature)
        return part

    def _function_response_part(
        self,
        *,
        Part: Any,
        name: str,
        response: dict[str, Any],
        call_id: str | None,
    ) -> Any:
        """Build a response part while preserving Gemini's call correlation ID."""
        part = Part.from_function_response(name=name, response=response)
        function_response = getattr(part, "function_response", None)
        if isinstance(call_id, str) and call_id and function_response is not None:
            setattr(function_response, "id", call_id)
        return part

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
            has_native_gemini_parts = role == "assistant" and isinstance(msg.get("gemini_parts"), list)
            if role in {"user", "assistant"} and text.strip() and not has_native_gemini_parts:
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
                gemini_parts = msg.get("gemini_parts")
                if isinstance(gemini_parts, list):
                    for item in gemini_parts:
                        if not isinstance(item, dict):
                            continue
                        restored = self._restore_model_part(item, Part=Part)
                        if restored is not None:
                            parts.append(restored)
                    if parts:
                        contents.append(Content(role="model", parts=parts))
                        continue
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
                call_id = msg.get("tool_call_id")
                parts.append(
                    self._function_response_part(
                        Part=Part,
                        name=tool_name,
                        response={"result": result_payload},
                        call_id=call_id if isinstance(call_id, str) else None,
                    )
                )
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
        texts: list[str] = []
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                if getattr(part, "thought", False) is True:
                    continue
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text:
                    texts.append(part_text)
        if texts:
            return "\n".join(texts).strip()
        direct_text = getattr(response, "text", None)
        return direct_text.strip() if isinstance(direct_text, str) else ""

    def _extract_reasoning_text(self, response: Any) -> str:
        texts: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", None)
                if getattr(part, "thought", False) is True and isinstance(text, str) and text:
                    texts.append(text)
        return "".join(texts).strip()

    def _extract_stream_text(self, response: Any) -> str:
        """Extract a delta without stripping meaningful whitespace."""
        texts: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "thought", False) is True:
                    continue
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str):
                    texts.append(part_text)
        if texts:
            return "".join(texts)
        try:
            direct_text = getattr(response, "text", None)
        except (AttributeError, TypeError, ValueError):
            direct_text = None
        return direct_text if isinstance(direct_text, str) else ""

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

    def _function_calling_config(self) -> dict[str, Any]:
        """Translate MTP's common tool-choice forms to google-genai config."""
        choice = self.tool_choice
        if isinstance(choice, str):
            normalized = choice.strip().lower()
            modes = {
                "auto": "AUTO",
                "none": "NONE",
                "required": "ANY",
                "any": "ANY",
                "validated": "VALIDATED",
            }
            if normalized in modes:
                return {"mode": modes[normalized]}
            if normalized:
                return {"mode": "ANY", "allowed_function_names": [choice]}
        elif isinstance(choice, dict):
            function = choice.get("function")
            name = function.get("name") if isinstance(function, dict) else choice.get("name")
            if isinstance(name, str) and name:
                return {"mode": "ANY", "allowed_function_names": [name]}
        raise ValueError(
            "Gemini tool_choice must be auto, none, required/any, validated, "
            "a function name, or a function-selection mapping"
        )

    def _base_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {"temperature": self.temperature}
        if self.response_schema is not None:
            config["response_schema"] = self.response_schema
        if self.response_json_schema is not None:
            config["response_json_schema"] = self.response_json_schema
        if self.response_schema is not None or self.response_json_schema is not None:
            config["response_mime_type"] = self.response_mime_type or "application/json"
        elif self.response_mime_type is not None:
            config["response_mime_type"] = self.response_mime_type
        return config

    def _action_request(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> dict[str, Any]:
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

        config = self._base_config()
        if system_instruction:
            config["system_instruction"] = system_instruction
        if genai_tools:
            config["tools"] = genai_tools
            config["automatic_function_calling"] = {"disable": True}
            config["tool_config"] = {
                "function_calling_config": self._function_calling_config()
            }
        return {"model": self.model_name, "contents": contents, "config": config}

    def _action_from_response(self, response: Any) -> AgentAction:
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "gemini", "model": self.model_name}
        if usage:
            action_meta["usage"] = usage
        reasoning = self._extract_reasoning_text(response)
        if reasoning:
            action_meta["reasoning"] = reasoning

        calls: list[ToolCall] = []
        serialized_tool_calls: list[dict[str, Any]] = []
        id_by_index: dict[int, str] = {}
        candidates = getattr(response, "candidates", None) or []
        response_parts: list[Any] = []
        if candidates:
            content = getattr(candidates[0], "content", None)
            response_parts = list(getattr(content, "parts", None) or [])
            for idx, part in enumerate(response_parts):
                fn = getattr(part, "function_call", None)
                if fn:
                    native_call_id = getattr(fn, "id", None)
                    call_id = (
                        native_call_id
                        if isinstance(native_call_id, str) and native_call_id
                        else f"gemini_call_{idx}"
                    )
                    id_by_index[idx] = call_id
                    raw_fn_args = getattr(fn, "args", None)
                    try:
                        raw_args = (
                            raw_fn_args
                            if isinstance(raw_fn_args, dict)
                            else dict(raw_fn_args or {})
                        )
                    except (TypeError, ValueError):
                        raw_args = {}
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
                            "function": {
                                "name": fn.name,
                                "arguments": json.dumps(raw_args, default=str),
                            },
                        }
                    )

        response_text = self._extract_response_text(response)
        if calls:
            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(calls),
                metadata={"provider": "gemini", "model": self.model_name},
            )
            return AgentAction(
                plan=plan,
                metadata={
                    **action_meta,
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": response_text,
                        "tool_calls": serialized_tool_calls,
                        "gemini_parts": self._serialize_model_parts(response_parts),
                    },
                },
            )

        return AgentAction(response_text=response_text, metadata=action_meta)

    def _finalize_request(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        contents, system_instruction = self._to_gemini_payload(messages)
        config = self._base_config()
        if system_instruction:
            config["system_instruction"] = system_instruction
        return {"model": self.model_name, "contents": contents, "config": config}

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        response = self._client.models.generate_content(**self._action_request(messages, tools))
        return self._action_from_response(response)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        del tool_results
        self._last_finalize_reasoning = None
        response = self._client.models.generate_content(**self._finalize_request(messages))
        self._last_finalize_usage = extract_usage_metrics(response) or None
        self._last_finalize_reasoning = self._extract_reasoning_text(response) or None
        text = self._extract_response_text(response)
        return text or "Done."

    def finalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> Iterator[str]:
        """Yield native ``google-genai`` text chunks and retain final usage."""
        del tool_results
        self._last_stream_usage = None
        self._last_stream_reasoning = None
        # Avoid leaking usage from an earlier non-streaming request if a stream
        # ends without a usage-bearing final chunk.
        self._last_finalize_usage = None
        stream = self._client.models.generate_content_stream(**self._finalize_request(messages))
        for chunk in stream:
            usage = extract_usage_metrics(chunk)
            if usage:
                self._last_stream_usage = usage
            reasoning = self._extract_reasoning_text(chunk)
            if reasoning:
                self._last_stream_reasoning = (self._last_stream_reasoning or "") + reasoning
            text = self._extract_stream_text(chunk)
            if text:
                yield text

    def capabilities(self) -> ProviderCapabilities:
        structured_output = (
            STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA
            if self.response_schema is not None or self.response_json_schema is not None
            else STRUCTURED_OUTPUT_CLIENT_VALIDATED
        )
        return ProviderCapabilities(
            provider="gemini",
            supports_tool_calling=True,
            supports_parallel_tool_calls=True,
            input_modalities=["text", "image", "audio", "video", "file"],
            supports_tool_media_output=True,
            supports_finalize_streaming=True,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=True,
            structured_output_support=structured_output,
            supports_native_async=True,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        response = await self._get_async_client().models.generate_content(
            **self._action_request(messages, tools)
        )
        return self._action_from_response(response)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        del tool_results
        self._last_finalize_reasoning = None
        response = await self._get_async_client().models.generate_content(
            **self._finalize_request(messages)
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        self._last_finalize_reasoning = self._extract_reasoning_text(response) or None
        text = self._extract_response_text(response)
        return text or "Done."

    async def afinalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> AsyncIterator[str]:
        """Yield Gemini's native async stream without occupying a worker thread."""
        del tool_results
        self._last_stream_usage = None
        self._last_stream_reasoning = None
        self._last_finalize_usage = None
        stream = self._get_async_client().models.generate_content_stream(
            **self._finalize_request(messages)
        )
        if inspect.isawaitable(stream):
            stream = await stream
        async for chunk in stream:
            usage = extract_usage_metrics(chunk)
            if usage:
                self._last_stream_usage = usage
            reasoning = self._extract_reasoning_text(chunk)
            if reasoning:
                self._last_stream_reasoning = (self._last_stream_reasoning or "") + reasoning
            text = self._extract_stream_text(chunk)
            if text:
                yield text
