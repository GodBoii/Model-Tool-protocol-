"""Configurable Chat Completions adapter with native tool and text streaming."""

from __future__ import annotations

import copy
import json
import math
import unicodedata
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from ..agent import AgentAction
from ..async_stream import async_from_sync
from ..protocol import ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    extract_usage_metrics,
    openai_like_tool_call_plan_payload,
)
from .openai_provider import OpenAIToolCallingProvider


def validate_endpoint(url: str) -> str:
    if (
        not isinstance(url, str)
        or not url
        or any(
            char.isspace() or unicodedata.category(char) in {"Cc", "Cf"} for char in url
        )
        or "\\" in url
    ):
        raise ValueError(
            "base_url must be a nonempty URL without whitespace or controls."
        )
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid host or port.") from exc
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or "@" in parsed.netloc
        or "%" in parsed.netloc
        or "?" in url
        or "#" in url
        or parsed.netloc.endswith(":")
        or port == 0
    ):
        raise ValueError(
            "base_url must be an HTTP(S) endpoint without credentials, query, or fragment."
        )
    if parsed.scheme == "http" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("Remote provider endpoints must use HTTPS.")
    return url.rstrip("/")


def read_value(value: Any, name: str, default: Any = None) -> Any:
    return (
        value.get(name, default)
        if isinstance(value, dict)
        else getattr(value, name, default)
    )


class OpenAICompatibleToolCallingProvider(OpenAIToolCallingProvider):
    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        provider_name: str = "compatible",
        temperature: float | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        parallel_tool_calls: bool | None = None,
        max_tokens: int | None = 1024,
        timeout_seconds: float = 60,
        extra_body: dict[str, Any] | None = None,
        input_modalities: tuple[str, ...] = ("text",),
        stream_include_usage: bool = True,
        client: Any | None = None,
    ) -> None:
        self.base_url = validate_endpoint(base_url)
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model cannot be empty.")
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens < 1):
            raise ValueError("max_tokens must be a positive integer or None.")
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive.")
        self.provider_name = provider_name
        if temperature is not None and (
            type(temperature) not in {int, float} or not math.isfinite(temperature)
        ):
            raise ValueError("temperature must be a finite number or None.")
        if parallel_tool_calls is not None and type(parallel_tool_calls) is not bool:
            raise TypeError("parallel_tool_calls must be a boolean or None.")
        if type(stream_include_usage) is not bool:
            raise TypeError("stream_include_usage must be a boolean.")
        self.max_tokens = max_tokens
        self.stream_include_usage = stream_include_usage
        if not isinstance(input_modalities, (tuple, list)):
            raise TypeError("input_modalities must be a tuple or list containing text.")
        self.input_modalities = tuple(input_modalities)
        if (
            "text" not in self.input_modalities
            or any(
                not isinstance(value, str) or value not in {"text", "image"}
                for value in self.input_modalities
            )
            or len(self.input_modalities) != len(set(self.input_modalities))
        ):
            raise ValueError(
                "This adapter supports text and optional image input only."
            )
        if extra_body is not None and not isinstance(extra_body, dict):
            raise TypeError("extra_body must be a dictionary or None.")
        self.extra_body = copy.deepcopy(extra_body or {})
        protected = {
            "model",
            "messages",
            "input",
            "tools",
            "tool_choice",
            "stream",
            "api_key",
            "parallel_tool_calls",
            "max_tokens",
            "max_completion_tokens",
            "temperature",
            "stream_options",
        }
        if protected.intersection(self.extra_body):
            raise ValueError(
                "extra_body cannot override protocol or credential fields."
            )
        if client is None:
            from openai import OpenAI

            if not isinstance(api_key, str) or not api_key.strip():
                raise ValueError(
                    "An explicit API key is required for a custom endpoint."
                )
            client = OpenAI(
                base_url=self.base_url, api_key=api_key, timeout=timeout_seconds
            )
        super().__init__(
            model=model,
            api_key=api_key,
            temperature=temperature,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            client=client,
        )
        self._last_stream_usage = None

    def _to_openai_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        from .common import format_openai_like_message

        formatted = []
        for message in messages:
            value = format_openai_like_message(
                message,
                allow_images="image" in self.input_modalities,
                allow_audio=False,
                allow_video=False,
                allow_files=False,
            )
            if value is None:
                continue
            if value["role"] == "assistant":
                if isinstance(value.get("tool_calls"), list):
                    value["tool_calls"] = [
                        {
                            **call,
                            "function": {
                                **call["function"],
                                "name": self._tool_names.wire_name(
                                    call["function"]["name"]
                                ),
                            },
                        }
                        for call in value["tool_calls"]
                    ]
                if isinstance(message.get("reasoning_content"), str):
                    value["reasoning_content"] = message["reasoning_content"]
            formatted.append(value)
        return formatted

    def _request(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        request = {"model": self.model, "messages": self._to_openai_messages(messages)}
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.max_tokens is not None:
            request["max_tokens"] = self.max_tokens
        if self.extra_body:
            request["extra_body"] = copy.deepcopy(self.extra_body)
        if tools:
            request["tools"] = self._to_openai_tools(tools)
            choice = copy.deepcopy(self.tool_choice)
            if isinstance(choice, dict) and isinstance(choice.get("function"), dict):
                choice["function"]["name"] = self._tool_names.wire_name(
                    choice["function"]["name"]
                )
            request["tool_choice"] = choice
            if self.parallel_tool_calls is not None:
                request["parallel_tool_calls"] = self.parallel_tool_calls
        if stream:
            request["stream"] = True
            if self.stream_include_usage:
                request["stream_options"] = {"include_usage": True}
        return request

    def _action(
        self,
        content: str,
        calls: list[Any],
        reasoning: str | None,
        usage: dict[str, int],
    ) -> AgentAction:
        metadata: dict[str, Any] = {"provider": self.provider_name, "model": self.model}
        if usage:
            metadata["usage"] = usage
        assistant = {"role": "assistant", "content": content}
        if reasoning:
            metadata["reasoning"] = reasoning
            assistant["reasoning_content"] = reasoning
        metadata["assistant_message"] = assistant
        if not calls:
            return AgentAction(response_text=content, metadata=metadata)
        call_ids: set[str] = set()
        for call in calls:
            function = read_value(call, "function")
            name = read_value(function, "name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Provider tool call is missing a function name.")
            if read_value(call, "type") not in {None, "function"}:
                raise ValueError("Provider returned an unsupported tool call type.")
            raw = read_value(function, "arguments", "")
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError as exc:
                raise ValueError("Provider returned malformed tool arguments.") from exc
            if not isinstance(parsed, dict):
                raise TypeError("Provider tool arguments must be a JSON object.")
            # Python's JSON decoder accepts NaN/Infinity, which are not JSON values.
            try:
                json.dumps(parsed, allow_nan=False)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "Provider tool arguments contain invalid JSON values."
                ) from exc
            identifier = read_value(call, "id")
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError("Provider tool call is missing its ID.")
            if identifier in call_ids:
                raise ValueError("Provider returned duplicate tool call IDs.")
            call_ids.add(identifier)
        payload = openai_like_tool_call_plan_payload(
            provider=self.provider_name,
            model=self.model,
            tool_calls=calls,
            content=content,
            reasoning=reasoning,
            use_current_index_refs=True,
        )
        for batch in payload["plan"].batches:
            for call in batch.calls:
                call.name = self._tool_names.original_name(call.name)
        for call in payload["metadata"]["assistant_tool_message"]["tool_calls"]:
            call["function"]["name"] = self._tool_names.original_name(
                call["function"]["name"]
            )
        if reasoning:
            payload["metadata"]["assistant_tool_message"]["reasoning_content"] = (
                reasoning
            )
        return AgentAction(
            plan=payload["plan"], metadata={**metadata, **payload["metadata"]}
        )

    def next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        response, limits = self._create_with_raw_headers(self._request(messages, tools))
        choices = read_value(response, "choices")
        if not choices:
            raise ValueError("Provider returned no response choices.")
        finish = read_value(choices[0], "finish_reason")
        if finish in {"length", "content_filter"}:
            raise ValueError(
                "Provider response was truncated or filtered; no tools executed."
            )
        if finish is not None and finish not in {"stop", "tool_calls"}:
            raise ValueError("Provider response returned an unsupported finish marker.")
        message = read_value(choices[0], "message")
        action = self._action(
            read_value(message, "content") or "",
            read_value(message, "tool_calls") or [],
            read_value(message, "reasoning_content"),
            extract_usage_metrics(response),
        )
        if limits:
            action.metadata["rate_limits"] = limits
        return action

    def stream_next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> Iterator[AgentAction | dict[str, Any]]:
        stream = self._client.chat.completions.create(
            **self._request(messages, tools, stream=True)
        )
        calls: dict[int, dict[str, Any]] = {}
        text = ""
        reasoning = ""
        usage: dict[str, int] = {}
        finished = False
        try:
            for chunk in stream:
                usage = extract_usage_metrics(chunk) or usage
                choices = read_value(chunk, "choices") or []
                if not choices:
                    continue
                finish = read_value(choices[0], "finish_reason")
                if finish in {"length", "content_filter"}:
                    raise ValueError(
                        "Provider stream was truncated or filtered; no tools executed."
                    )
                if finish is not None and finish not in {"stop", "tool_calls"}:
                    raise ValueError(
                        "Provider stream returned an unsupported finish marker."
                    )
                delta = read_value(choices[0], "delta")
                if finished and any(
                    read_value(delta, key)
                    for key in ("content", "reasoning_content", "tool_calls")
                ):
                    raise ValueError(
                        "Provider stream returned output after its finish marker."
                    )
                finished = finished or finish is not None
                part = read_value(delta, "content")
                if part:
                    text += part
                    yield {"type": "text_chunk", "chunk": part}
                thought = read_value(delta, "reasoning_content")
                if thought:
                    reasoning += thought
                    yield {"type": "reasoning_chunk", "chunk": thought}
                for call in read_value(delta, "tool_calls") or []:
                    if read_value(call, "type") not in {None, "function"}:
                        raise ValueError(
                            "Provider returned an unsupported tool call type."
                        )
                    index = read_value(call, "index")
                    if type(index) is not int or index < 0:
                        raise ValueError("Tool stream fragment has an invalid index.")
                    item = calls.setdefault(
                        index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    identifier = read_value(call, "id")
                    if identifier:
                        if item["id"] and item["id"] != identifier:
                            raise ValueError("Tool stream changed its call ID.")
                        item["id"] = identifier
                    function = read_value(call, "function")
                    for key in ("name", "arguments"):
                        fragment = read_value(function, key)
                        if fragment is not None and not isinstance(fragment, str):
                            raise ValueError("Tool stream fragments must be strings.")
                        item["function"][key] += fragment or ""
            if not finished:
                raise ValueError("Provider stream ended without a finish marker.")
            yield self._action(
                text, [calls[i] for i in sorted(calls)], reasoning or None, usage
            )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    async def astream_next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ):
        async for item in async_from_sync(
            lambda: self.stream_next_action(messages, tools)
        ):
            yield item

    def finalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        action = self.next_action(messages, [])
        if action.plan:
            raise ValueError("Provider requested tools during finalization.")
        self._last_finalize_message = action.metadata["assistant_message"]
        self._last_finalize_usage = action.metadata.get("usage")
        return action.response_text or ""

    def finalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> Iterator[str]:
        self._last_stream_usage = None
        for item in self.stream_next_action(messages, []):
            if isinstance(item, AgentAction):
                if item.plan:
                    raise ValueError("Provider requested tools during finalization.")
                self._last_finalize_message = item.metadata["assistant_message"]
                self._last_stream_usage = item.metadata.get("usage")
            elif item["type"] == "text_chunk":
                yield item["chunk"]

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider_name,
            supports_parallel_tool_calls=bool(self.parallel_tool_calls),
            input_modalities=list(self.input_modalities),
            supports_finalize_streaming=True,
            usage_metrics_quality="rich",
            supports_reasoning_metadata=True,
            structured_output_support="client_validated",
        )
