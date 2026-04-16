from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..media import Image
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    STRUCTURED_OUTPUT_CLIENT_VALIDATED,
    USAGE_METRICS_RICH,
    calls_to_dependency_batches,
    extract_refs,
    normalize_refs,
)


def _read_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


class OllamaToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for a local Ollama server.

    Default host:
    http://localhost:11434
    """

    def __init__(
        self,
        *,
        model: str = "qwen3",
        host: str | None = None,
        api_key: str | None = None,
        options: dict[str, Any] | None = None,
        format: dict[str, Any] | str | None = None,
        keep_alive: float | str | None = None,
        think: bool | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.host = host
        self.options = options
        self.format = format
        self.keep_alive = keep_alive
        self.think = think
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._last_stream_thinking: str | None = None  # Track thinking from stream
        self._client = client or self._make_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from ollama import Client
        except ImportError as exc:
            raise ImportError(
                "`ollama` not installed. Install with: pip install ollama"
            ) from exc

        resolved_api_key = api_key or os.getenv("OLLAMA_API_KEY")
        client_kwargs: dict[str, Any] = {}
        if self.host:
            client_kwargs["host"] = self.host
        if resolved_api_key:
            client_kwargs["headers"] = {"authorization": f"Bearer {resolved_api_key}"}
        return Client(**client_kwargs)

    def _extract_usage_metrics(self, response: Any) -> dict[str, int]:
        prompt_tokens = _read_value(response, "prompt_eval_count")
        completion_tokens = _read_value(response, "eval_count")
        metrics: dict[str, int] = {}
        if isinstance(prompt_tokens, int):
            metrics["input_tokens"] = prompt_tokens
        if isinstance(completion_tokens, int):
            metrics["output_tokens"] = completion_tokens
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            metrics["total_tokens"] = prompt_tokens + completion_tokens
        return metrics

    def _image_payload(self, image: Image) -> bytes | str | None:
        if image.filepath:
            return str(Path(str(image.filepath)))
        raw = image.get_content_bytes()
        if raw is not None:
            return raw
        return image.url

    def _assistant_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments_value = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments_value = {"_raw_arguments": arguments}
            elif isinstance(arguments, dict):
                arguments_value = arguments
            else:
                arguments_value = {}
            formatted.append(
                {
                    "id": tool_call.get("id"),
                    "type": "function",
                    "function": {
                        "name": function.get("name"),
                        "arguments": arguments_value,
                    },
                }
            )
        return formatted

    def _to_ollama_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                continue

            entry: dict[str, Any] = {
                "role": role,
                "content": msg.get("content") if isinstance(msg.get("content"), str) else json.dumps(msg.get("content", "")),
            }
            if role == "assistant":
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    formatted_calls = self._assistant_tool_calls(tool_calls)
                    if formatted_calls:
                        entry["tool_calls"] = formatted_calls
                reasoning = msg.get("reasoning")
                if isinstance(reasoning, str) and reasoning.strip():
                    entry["thinking"] = reasoning
            elif role == "tool":
                entry["tool_name"] = msg.get("tool_name")
            elif role == "user":
                images = msg.get("images")
                if isinstance(images, list):
                    image_payloads = []
                    for image in images:
                        if isinstance(image, Image):
                            payload = self._image_payload(image)
                            if payload is not None:
                                image_payloads.append(payload)
                    if image_payloads:
                        entry["images"] = image_payloads
            formatted.append(entry)
        return formatted

    def _to_ollama_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
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

    def _request_kwargs(self, tools: list[ToolSpec] | None = None, *, stream: bool = False) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.model, "stream": stream}
        if self.options is not None:
            kwargs["options"] = self.options
        if self.format is not None:
            kwargs["format"] = self.format
        if self.keep_alive is not None:
            kwargs["keep_alive"] = self.keep_alive
        if self.think is not None:
            kwargs["think"] = self.think
        if tools:
            kwargs["tools"] = self._to_ollama_tools(tools)
        return kwargs

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        ollama_messages = self._to_ollama_messages(messages)
        response = self._client.chat(
            messages=ollama_messages,
            **self._request_kwargs(tools),
        )

        message = _read_value(response, "message") or {}
        content = _read_value(message, "content") or ""
        tool_calls = _read_value(message, "tool_calls")
        reasoning = _read_value(message, "thinking")
        usage = self._extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "ollama", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if isinstance(reasoning, str) and reasoning.strip():
            action_meta["reasoning"] = reasoning.strip()

        if isinstance(tool_calls, list) and tool_calls:
            mtp_calls: list[ToolCall] = []
            serialized_tool_calls: list[dict[str, Any]] = []
            id_by_index: dict[int, str] = {}
            call_reasoning = reasoning.strip() if isinstance(reasoning, str) and reasoning.strip() else None
            for idx, tc in enumerate(tool_calls):
                function = _read_value(tc, "function") or {}
                call_id = _read_value(tc, "id") or f"call_{idx}"
                id_by_index[idx] = call_id
                arguments = _read_value(function, "arguments")
                if isinstance(arguments, dict):
                    parsed_args = arguments
                elif isinstance(arguments, str):
                    try:
                        parsed_args = json.loads(arguments)
                    except json.JSONDecodeError:
                        parsed_args = {"_raw_arguments": arguments}
                else:
                    parsed_args = {}
                normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=idx)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                tool_name = _read_value(function, "name") or ""
                mtp_calls.append(
                    ToolCall(
                        id=call_id,
                        name=tool_name,
                        arguments=normalized_args,
                        depends_on=depends_on,
                        reasoning=call_reasoning,
                    )
                )
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": json.dumps(parsed_args)},
                        "reasoning": call_reasoning,
                    }
                )

            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "ollama", "model": self.model},
            )
            return AgentAction(
                plan=plan,
                metadata={
                    **action_meta,
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": serialized_tool_calls,
                        "reasoning": reasoning,
                    },
                },
            )

        return AgentAction(response_text=content, metadata=action_meta)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        ollama_messages = self._to_ollama_messages(messages)
        response = self._client.chat(messages=ollama_messages, **self._request_kwargs())
        self._last_finalize_usage = self._extract_usage_metrics(response) or None
        message = _read_value(response, "message") or {}
        tool_calls = _read_value(message, "tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        content = _read_value(message, "content")
        return content or "Done."

    def finalize_stream(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> Iterator[str]:
        ollama_messages = self._to_ollama_messages(messages)
        self._last_stream_usage = None
        self._last_stream_thinking = None  # Track thinking from stream
        stream = self._client.chat(messages=ollama_messages, **self._request_kwargs(stream=True))
        for chunk in stream:
            usage = self._extract_usage_metrics(chunk)
            if usage:
                self._last_stream_usage = usage
            
            # Extract thinking tokens from stream chunks
            message = _read_value(chunk, "message") or {}
            thinking = _read_value(message, "thinking")
            if thinking and isinstance(thinking, str):
                self._last_stream_thinking = thinking
            
            content = _read_value(message, "content")
            if content:
                yield content

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="ollama",
            supports_tool_calling=True,
            supports_parallel_tool_calls=True,
            input_modalities=["text", "image"],
            supports_tool_media_output=True,
            supports_finalize_streaming=True,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=bool(self.think),
            structured_output_support=STRUCTURED_OUTPUT_CLIENT_VALIDATED,
            supports_native_async=False,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
