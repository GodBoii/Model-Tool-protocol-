from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from ..media import Audio, File, Image, Video
from ..protocol import ExecutionPlan, ToolBatch, ToolCall, ToolSpec

USAGE_METRICS_NONE = "none"
USAGE_METRICS_BASIC = "basic"
USAGE_METRICS_RICH = "rich"

STRUCTURED_OUTPUT_NONE = "none"
STRUCTURED_OUTPUT_CLIENT_VALIDATED = "client_validated"
STRUCTURED_OUTPUT_NATIVE_JSON_OBJECT = "native_json_object"
STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA = "native_json_schema"


@dataclass(slots=True)
class ProviderCapabilities:
    provider: str
    supports_tool_calling: bool = True
    supports_parallel_tool_calls: bool = False
    input_modalities: list[str] = field(default_factory=lambda: ["text"])
    supports_tool_media_output: bool = False
    supports_finalize_streaming: bool = False
    usage_metrics_quality: str = USAGE_METRICS_BASIC
    supports_reasoning_metadata: bool = False
    structured_output_support: str = STRUCTURED_OUTPUT_NONE
    supports_native_async: bool = False
    allow_finalize_stream_fallback: bool = True

    def supports_input_modality(self, modality: str) -> bool:
        return modality in set(self.input_modalities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "supports_tool_calling": self.supports_tool_calling,
            "supports_parallel_tool_calls": self.supports_parallel_tool_calls,
            "input_modalities": sorted(set(self.input_modalities)),
            "supports_tool_media_output": self.supports_tool_media_output,
            "supports_finalize_streaming": self.supports_finalize_streaming,
            "usage_metrics_quality": self.usage_metrics_quality,
            "supports_reasoning_metadata": self.supports_reasoning_metadata,
            "structured_output_support": self.structured_output_support,
            "supports_native_async": self.supports_native_async,
            "allow_finalize_stream_fallback": self.allow_finalize_stream_fallback,
        }


def capabilities_from_any(value: Any) -> ProviderCapabilities | None:
    if value is None:
        return None
    if isinstance(value, ProviderCapabilities):
        return value
    if isinstance(value, dict):
        provider = str(value.get("provider") or "unknown")
        input_modalities = value.get("input_modalities")
        if not isinstance(input_modalities, list):
            input_modalities = ["text"]
        normalized_modalities = [str(item) for item in input_modalities if isinstance(item, (str, int, float))]
        return ProviderCapabilities(
            provider=provider,
            supports_tool_calling=bool(value.get("supports_tool_calling", True)),
            supports_parallel_tool_calls=bool(value.get("supports_parallel_tool_calls", False)),
            input_modalities=normalized_modalities or ["text"],
            supports_tool_media_output=bool(value.get("supports_tool_media_output", False)),
            supports_finalize_streaming=bool(value.get("supports_finalize_streaming", False)),
            usage_metrics_quality=str(value.get("usage_metrics_quality", USAGE_METRICS_BASIC)),
            supports_reasoning_metadata=bool(value.get("supports_reasoning_metadata", False)),
            structured_output_support=str(value.get("structured_output_support", STRUCTURED_OUTPUT_NONE)),
            supports_native_async=bool(value.get("supports_native_async", False)),
            allow_finalize_stream_fallback=bool(value.get("allow_finalize_stream_fallback", True)),
        )
    return None


def _read_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _first_int(source: Any, *keys: str) -> int | None:
    for key in keys:
        value = _coerce_int(_read_value(source, key))
        if value is not None:
            return value
    return None


def extract_usage_metrics(response: Any) -> dict[str, int]:
    usage = _read_value(response, "usage")
    if usage is None:
        usage = _read_value(response, "usage_metadata")
    if usage is None:
        usage = _read_value(response, "usageMetadata")
    if usage is None:
        return {}

    prompt_tokens = _first_int(
        usage,
        "prompt_tokens",
        "input_tokens",
        "prompt_token_count",
        "promptTokenCount",
        "inputTokenCount",
    )

    completion_tokens = _first_int(
        usage,
        "completion_tokens",
        "output_tokens",
        "candidates_token_count",
        "response_token_count",
        "completionTokenCount",
        "outputTokenCount",
        "candidatesTokenCount",
        "responseTokenCount",
    )

    total_tokens = _first_int(
        usage,
        "total_tokens",
        "total_token_count",
        "totalTokenCount",
    )
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    reasoning_tokens: int | None = None
    completion_details = _read_value(usage, "completion_tokens_details")
    if completion_details is None:
        completion_details = _read_value(usage, "completionTokensDetails")
    if completion_details is not None:
        reasoning_tokens = _first_int(completion_details, "reasoning_tokens", "reasoningTokenCount")
    if reasoning_tokens is None:
        output_token_details = _read_value(usage, "output_token_details")
        if output_token_details is None:
            output_token_details = _read_value(usage, "outputTokenDetails")
        if output_token_details is not None:
            reasoning_tokens = _first_int(output_token_details, "reasoning", "reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = _first_int(usage, "thoughts_token_count", "thoughtsTokenCount")

    prompt_details = _read_value(usage, "prompt_tokens_details")
    if prompt_details is None:
        prompt_details = _read_value(usage, "promptTokensDetails")
    cached_input_tokens = None
    cache_write_tokens = None
    if prompt_details is not None:
        cached_input_tokens = _first_int(prompt_details, "cached_tokens", "cachedTokens")
        cache_write_tokens = _first_int(prompt_details, "cache_write_tokens", "cacheWriteTokens")
    if cached_input_tokens is None:
        cached_input_tokens = _first_int(
            usage,
            "cached_content_token_count",
            "cachedContentTokenCount",
        )

    cache_creation_input_tokens = _first_int(
        usage,
        "cache_creation_input_tokens",
        "cacheCreationInputTokens",
    )
    cache_read_input_tokens = _first_int(
        usage,
        "cache_read_input_tokens",
        "cacheReadInputTokens",
    )
    tool_use_prompt_tokens = _first_int(
        usage,
        "tool_use_prompt_token_count",
        "toolUsePromptTokenCount",
    )

    metrics: dict[str, int] = {}
    if prompt_tokens is not None:
        metrics["input_tokens"] = prompt_tokens
    if completion_tokens is not None:
        metrics["output_tokens"] = completion_tokens
    if total_tokens is not None:
        metrics["total_tokens"] = total_tokens
    if reasoning_tokens is not None:
        metrics["reasoning_tokens"] = reasoning_tokens
    if cached_input_tokens is not None:
        metrics["cached_input_tokens"] = cached_input_tokens
    if cache_write_tokens is not None:
        metrics["cache_write_tokens"] = cache_write_tokens
    if cache_creation_input_tokens is not None:
        metrics["cache_creation_input_tokens"] = cache_creation_input_tokens
    if cache_read_input_tokens is not None:
        metrics["cache_read_input_tokens"] = cache_read_input_tokens
    if tool_use_prompt_tokens is not None:
        metrics["tool_use_prompt_tokens"] = tool_use_prompt_tokens
    return metrics


def extract_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        if "$ref" in value and isinstance(value["$ref"], str):
            refs.append(value["$ref"])
        for item in value.values():
            refs.extend(extract_refs(item))
        return refs
    if isinstance(value, list):
        for item in value:
            refs.extend(extract_refs(item))
    return refs


def _normalize_ref_value(ref_value: Any, id_by_index: dict[int, str], current_idx: int | None) -> Any:
    known_ids = set(id_by_index.values())
    if isinstance(ref_value, str) and ref_value in known_ids:
        return ref_value
    if isinstance(ref_value, int):
        return id_by_index.get(ref_value, ref_value)
    if not isinstance(ref_value, str):
        return ref_value

    stripped = ref_value.strip()
    if stripped in known_ids:
        return stripped
    if stripped.isdigit():
        return id_by_index.get(int(stripped), stripped)

    lowered = stripped.lower()
    if lowered in ("result", "last", "last_call", "prev", "previous"):
        target_idx = current_idx - 1 if current_idx is not None else 0
        return id_by_index.get(max(0, target_idx), stripped)

    call_match = re.fullmatch(r"call_(\d+)", lowered)
    if call_match:
        return id_by_index.get(int(call_match.group(1)), stripped)

    c_match = re.fullmatch(r"c(\d+)", lowered)
    if c_match:
        one_based_idx = int(c_match.group(1)) - 1
        if one_based_idx >= 0:
            return id_by_index.get(one_based_idx, stripped)

    return stripped


def normalize_refs(value: Any, id_by_index: dict[int, str], current_idx: int | None = None) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "$ref":
                normalized[key] = _normalize_ref_value(item, id_by_index, current_idx)
            else:
                normalized[key] = normalize_refs(item, id_by_index, current_idx)
        return normalized
    if isinstance(value, list):
        return [normalize_refs(item, id_by_index, current_idx) for item in value]
    return value


def safe_load_arguments(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    raw = raw_args if isinstance(raw_args, str) and raw_args else "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_arguments": raw}
    if isinstance(parsed, dict):
        return parsed
    return {"_raw_arguments": raw}


def calls_to_dependency_batches(calls: list[ToolCall]) -> list[ToolBatch]:
    remaining: dict[str, ToolCall] = {call.id: call for call in calls}
    ordered_ids = [call.id for call in calls]
    done: set[str] = set()
    batches: list[ToolBatch] = []

    while remaining:
        ready_ids = [
            call_id
            for call_id in ordered_ids
            if call_id in remaining and all(dep in done for dep in remaining[call_id].depends_on)
        ]
        if not ready_ids:
            unresolved_calls = [remaining[call_id] for call_id in ordered_ids if call_id in remaining]
            batches.append(ToolBatch(mode="sequential", calls=unresolved_calls))
            break

        ready_calls = [remaining.pop(call_id) for call_id in ready_ids]
        mode = "parallel" if len(ready_calls) > 1 else "sequential"
        batches.append(ToolBatch(mode=mode, calls=ready_calls))
        done.update(ready_ids)

    return batches


def openai_like_tool_call_plan_payload(
    *,
    provider: str,
    model: str,
    tool_calls: list[Any],
    content: str = "",
    reasoning: str | None = None,
    tool_call_source: str | None = None,
    use_current_index_refs: bool = False,
) -> dict[str, Any]:
    mtp_calls: list[ToolCall] = []
    serialized_tool_calls: list[dict[str, Any]] = []
    parsed_calls: list[tuple[int, str, str, dict[str, Any], str]] = []
    id_by_index: dict[int, str] = {}
    call_reasoning = reasoning.strip() if isinstance(reasoning, str) and reasoning.strip() else None

    for idx, tc in enumerate(tool_calls):
        if isinstance(tc, dict):
            function = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            fn_name = str(function.get("name") or tc.get("name") or "")
            raw_args_value = function.get("arguments") or tc.get("arguments") or "{}"
            parsed_args = tc.get("arguments") if isinstance(tc.get("arguments"), dict) else None
            raw_args = raw_args_value if isinstance(raw_args_value, str) else json.dumps(raw_args_value, default=str)
            call_id = str(tc.get("id") or f"call_{idx}")
        else:
            function = getattr(tc, "function", None)
            fn_name = str(getattr(function, "name", "") or "")
            raw_args_value = getattr(function, "arguments", None) or "{}"
            parsed_args = None
            raw_args = raw_args_value if isinstance(raw_args_value, str) else json.dumps(raw_args_value, default=str)
            call_id = str(getattr(tc, "id", None) or f"call_{idx}")
        if not fn_name:
            raise RuntimeError(f"{provider} tool call {call_id!r} is missing a function name.")
        id_by_index[idx] = call_id
        if parsed_args is None:
            parsed_args = safe_load_arguments(raw_args)
        parsed_calls.append((idx, call_id, fn_name, parsed_args, raw_args))
        serialized_tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": fn_name, "arguments": raw_args or "{}"},
                "reasoning": call_reasoning,
            }
        )

    for idx, call_id, fn_name, parsed_args, _raw_args in parsed_calls:
        current_idx = idx if use_current_index_refs else None
        normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=current_idx)
        depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
        mtp_calls.append(
            ToolCall(
                id=call_id,
                name=fn_name,
                arguments=normalized_args,
                depends_on=depends_on,
                reasoning=call_reasoning,
            )
        )

    plan = ExecutionPlan(
        batches=calls_to_dependency_batches(mtp_calls),
        metadata={"provider": provider, "model": model},
    )
    metadata: dict[str, Any] = {
        "raw_tool_call_count": len(tool_calls),
        "derived_batch_count": len(plan.batches),
        "derived_batch_modes": [batch.mode for batch in plan.batches],
        "assistant_tool_message": {
            "role": "assistant",
            "content": content,
            "tool_calls": serialized_tool_calls,
        },
    }
    if tool_call_source:
        metadata["tool_call_source"] = tool_call_source
    if call_reasoning:
        metadata["assistant_tool_message"]["reasoning"] = call_reasoning
    return {"plan": plan, "metadata": metadata}


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, default=str)
    except Exception:
        return str(content)


def _fetch_url_bytes(url: str) -> bytes | None:
    try:
        request = Request(url, headers={"User-Agent": "MTP-SDK/0.1"})
        with urlopen(request, timeout=20) as response:  # noqa: S310
            return response.read()
    except Exception:
        return None


def _guess_mime_from_path(path_or_name: str, default: str) -> str:
    guessed = mimetypes.guess_type(path_or_name)[0]
    return guessed or default


def _to_data_url(payload: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(payload).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _image_to_openai_part(image: Image) -> dict[str, Any] | None:
    if image.url:
        return {"type": "image_url", "image_url": {"url": image.url}}
    content = image.get_content_bytes()
    if content is None:
        return None
    mime_type = image.mime_type
    if mime_type is None:
        if image.format:
            mime_type = f"image/{image.format}"
        elif image.filepath:
            mime_type = _guess_mime_from_path(str(image.filepath), "image/jpeg")
        else:
            mime_type = "image/jpeg"
    image_url = _to_data_url(content, mime_type)
    part: dict[str, Any] = {"type": "image_url", "image_url": {"url": image_url}}
    if image.detail:
        part["image_url"]["detail"] = image.detail
    return part


def _audio_to_openai_part(audio: Audio) -> dict[str, Any] | None:
    content = audio.get_content_bytes()
    if content is None and audio.url:
        content = _fetch_url_bytes(audio.url)
    if content is None:
        return None
    audio_format = audio.format
    if audio_format is None:
        if audio.filepath:
            audio_format = Path(str(audio.filepath)).suffix.lstrip(".") or "wav"
        elif audio.mime_type and "/" in audio.mime_type:
            audio_format = audio.mime_type.split("/", 1)[1]
        else:
            audio_format = "wav"
    encoded = base64.b64encode(content).decode("utf-8")
    return {"type": "input_audio", "input_audio": {"data": encoded, "format": audio_format}}


def _file_to_openai_part(file: File) -> dict[str, Any] | None:
    if file.content is None and file.filepath is None and file.url is None:
        return None
    raw = file.get_content_bytes()
    if raw is None and file.url:
        raw = _fetch_url_bytes(file.url)
    if raw is None:
        return None
    filename = file.filename
    if filename is None and file.filepath is not None:
        filename = Path(str(file.filepath)).name
    if filename is None and file.url:
        filename = Path(file.url).name or "file"
    if filename is None:
        filename = "file"
    mime_type = file.mime_type or _guess_mime_from_path(filename, "application/octet-stream")
    data_url = _to_data_url(raw, mime_type)
    return {"type": "file", "file": {"filename": filename, "file_data": data_url}}


def format_openai_like_message(
    msg: dict[str, Any],
    *,
    allow_images: bool = True,
    allow_audio: bool = True,
    allow_video: bool = False,
    allow_files: bool = True,
) -> dict[str, Any] | None:
    role = msg.get("role")
    if role not in {"system", "user", "assistant", "tool"}:
        return None

    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": msg.get("tool_call_id"),
            "content": _message_content_text(msg.get("content", "")),
        }

    new_msg: dict[str, Any] = {"role": role, "content": msg.get("content") or ""}
    if "tool_calls" in msg:
        new_msg["tool_calls"] = msg["tool_calls"]

    media_parts: list[dict[str, Any]] = []
    images = msg.get("images") if isinstance(msg.get("images"), list) else None
    audios = msg.get("audios") if isinstance(msg.get("audios"), list) else None
    if audios is None and isinstance(msg.get("audio"), list):
        audios = msg.get("audio")
    videos = msg.get("videos") if isinstance(msg.get("videos"), list) else None
    files = msg.get("files") if isinstance(msg.get("files"), list) else None

    if allow_images and images:
        for image in images:
            if isinstance(image, Image):
                image_part = _image_to_openai_part(image)
                if image_part is not None:
                    media_parts.append(image_part)

    if allow_audio and audios:
        for audio in audios:
            if isinstance(audio, Audio):
                audio_part = _audio_to_openai_part(audio)
                if audio_part is not None:
                    media_parts.append(audio_part)

    if allow_video and videos:
        for video in videos:
            if isinstance(video, Video) and video.url:
                media_parts.append({"type": "text", "text": f"[video] {video.url}"})

    if allow_files and files:
        for file in files:
            if isinstance(file, File):
                file_part = _file_to_openai_part(file)
                if file_part is not None:
                    media_parts.append(file_part)

    if media_parts:
        text = _message_content_text(msg.get("content", ""))
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        parts.extend(media_parts)
        new_msg["content"] = parts
    return new_msg
