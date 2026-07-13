from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    USAGE_METRICS_RICH,
    STRUCTURED_OUTPUT_CLIENT_VALIDATED,
    STRUCTURED_OUTPUT_NATIVE_JSON_OBJECT,
    STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA,
    aiter_openai_like_stream_content,
    extract_usage_metrics,
    format_openai_like_message,
    iter_openai_like_stream_content,
    openai_like_tool_call_plan_payload,
)


def _value(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


@dataclass(slots=True)
class _CallDelta:
    id: str = ""
    name: str = ""
    arguments: str = ""


class _ActionStream:
    """Accumulate interleaved Chat Completions deltas by tool-call index."""

    def __init__(self) -> None:
        self.content: list[str] = []
        self.calls: dict[int, _CallDelta] = {}
        self.usage: dict[str, int] | None = None

    def add(self, chunk: Any) -> str | None:
        usage = extract_usage_metrics(chunk)
        if usage:
            self.usage = usage
        choices = _value(chunk, "choices")
        delta = _value(choices[0], "delta") if choices else None
        if delta is None:
            return None
        content = _value(delta, "content")
        text = content if isinstance(content, str) and content else None
        if text:
            self.content.append(text)
        for fragment in _value(delta, "tool_calls") or []:
            raw_index = _value(fragment, "index")
            if isinstance(raw_index, bool):
                continue
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if index < 0:
                continue
            call = self.calls.setdefault(index, _CallDelta())
            call_id = _value(fragment, "id")
            if isinstance(call_id, str):
                call.id += call_id
            function = _value(fragment, "function")
            if function is not None:
                name = _value(function, "name")
                arguments = _value(function, "arguments")
                if isinstance(name, str):
                    call.name += name
                if isinstance(arguments, str):
                    call.arguments += arguments
        return text

    def tool_calls(self) -> list[dict[str, Any]]:
        return [
            {
                "id": call.id or f"call_{index}",
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments or "{}"},
            }
            for index, call in sorted(self.calls.items())
        ]


class OpenAIToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for OpenAI.
    Supports tool calling for GPT-4o, GPT-4, and GPT-3.5-turbo.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        api_key: str | None = None,
        temperature: float | None = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        parallel_tool_calls: bool = True,
        strict_tools: bool = False,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        max_completion_tokens: int | None = None,
        stream_include_usage: bool = True,
        stream_include_obfuscation: bool | None = None,
        timeout: float | None = None,
        client: Any | None = None,
        async_client: Any | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.parallel_tool_calls = parallel_tool_calls
        self.strict_tools = strict_tools
        self.response_format = response_format
        self.reasoning_effort = reasoning_effort
        self.max_completion_tokens = max_completion_tokens
        self.stream_include_usage = stream_include_usage
        self.stream_include_obfuscation = stream_include_obfuscation
        self.timeout = timeout
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._last_rate_limits: dict[str, Any] | None = None
        self._last_finalize_rate_limits: dict[str, Any] | None = None
        self._api_key = api_key
        self._client = client or self._make_client(api_key=api_key)
        self._async_client = async_client

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. Please install using `pip install openai`"
            ) from exc

        key = api_key or require_env("OPENAI_API_KEY")
        return OpenAI(api_key=key)

    def _get_async_client(self) -> Any:
        if self._async_client is not None:
            return self._async_client
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. Please install using `pip install openai`"
            ) from exc
        key = self._api_key or require_env("OPENAI_API_KEY")
        self._async_client = AsyncOpenAI(api_key=key)
        return self._async_client

    def _to_openai_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            converted = format_openai_like_message(
                msg,
                allow_images=True,
                allow_audio=True,
                allow_video=False,
                allow_files=True,
            )
            if converted is not None:
                formatted.append(converted)
        return formatted

    def _to_openai_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            function = {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            }
            if self.strict_tools:
                function["strict"] = True
            converted.append({
                "type": "function",
                "function": function,
            })
        return converted

    def _request_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if self.response_format is not None:
            options["response_format"] = self.response_format
        if self.reasoning_effort is not None:
            options["reasoning_effort"] = self.reasoning_effort
        if self.max_completion_tokens is not None:
            options["max_completion_tokens"] = self.max_completion_tokens
        if self.timeout is not None:
            options["timeout"] = self.timeout
        return options

    def _base_request_args(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        args: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            **self._request_options(),
        }
        # Some reasoning-only model snapshots reject sampling parameters. A
        # caller can select those models without sending temperature by using
        # ``temperature=None``.
        if self.temperature is not None:
            args["temperature"] = self.temperature
        return args

    def _stream_options(self) -> dict[str, bool] | None:
        options: dict[str, bool] = {}
        if self.stream_include_usage:
            options["include_usage"] = True
        if self.stream_include_obfuscation is not None:
            options["include_obfuscation"] = self.stream_include_obfuscation
        return options or None

    def _stream_request_args(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        args = {**self._base_request_args(messages), "stream": True}
        stream_options = self._stream_options()
        if stream_options is not None:
            args["stream_options"] = stream_options
        return args

    def _extract_rate_limits(self, headers: Any) -> dict[str, Any] | None:
        if headers is None:
            return None
        try:
            items = list(headers.items()) if hasattr(headers, "items") else []
        except Exception:
            items = []
        if not items:
            return None
        collected: dict[str, Any] = {}
        for key, value in items:
            key_s = str(key)
            lower = key_s.lower()
            if lower.startswith("x-ratelimit-") or lower == "retry-after":
                collected[key_s] = value
        return collected or None

    def _create_with_raw_headers(self, request_args: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
        raw_api = getattr(self._client.chat.completions, "with_raw_response", None)
        if raw_api is None or not hasattr(raw_api, "create"):
            response = self._client.chat.completions.create(**request_args)
            return response, None
        raw_response = raw_api.create(**request_args)
        parsed = raw_response.parse() if hasattr(raw_response, "parse") else raw_response
        headers = getattr(raw_response, "headers", None)
        return parsed, self._extract_rate_limits(headers)

    async def _acreate_with_raw_headers(
        self, request_args: dict[str, Any]
    ) -> tuple[Any, dict[str, Any] | None]:
        completions = self._get_async_client().chat.completions
        raw_api = getattr(completions, "with_raw_response", None)
        if raw_api is None or not hasattr(raw_api, "create"):
            return await completions.create(**request_args), None
        raw_response = await raw_api.create(**request_args)
        parsed = raw_response.parse() if hasattr(raw_response, "parse") else raw_response
        if inspect.isawaitable(parsed):
            parsed = await parsed
        headers = getattr(raw_response, "headers", None)
        return parsed, self._extract_rate_limits(headers)

    def _action_from_response(
        self, response: Any, rate_limits: dict[str, Any] | None
    ) -> AgentAction:
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "openai", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if rate_limits:
            action_meta["rate_limits"] = rate_limits

        if tool_calls:
            payload = openai_like_tool_call_plan_payload(
                provider="openai",
                model=self.model,
                tool_calls=list(tool_calls),
                content=message.content or "",
                tool_call_source="native_tool_calls",
            )
            return AgentAction(
                plan=payload["plan"],
                metadata={**action_meta, **payload["metadata"]},
            )
        return AgentAction(response_text=message.content or "", metadata=action_meta)

    def _finalize_text_from_response(self, response: Any) -> str:
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        openai_tools = self._to_openai_tools(tools)

        request_args = self._base_request_args(messages)
        if openai_tools:
            request_args["tools"] = openai_tools
            request_args["tool_choice"] = self.tool_choice
            request_args["parallel_tool_calls"] = self.parallel_tool_calls

        response, rate_limits = self._create_with_raw_headers(request_args)
        self._last_rate_limits = rate_limits
        return self._action_from_response(response, rate_limits)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        response, rate_limits = self._create_with_raw_headers(
            self._base_request_args(messages)
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        self._last_finalize_rate_limits = rate_limits
        return self._finalize_text_from_response(response)

    def finalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> Iterator[str]:
        self._last_stream_usage = None
        request_args = self._stream_request_args(messages)
        stream = self._client.chat.completions.create(**request_args)

        def remember_usage(usage: dict[str, int]) -> None:
            self._last_stream_usage = usage

        yield from iter_openai_like_stream_content(stream, on_usage=remember_usage)

    def capabilities(self) -> ProviderCapabilities:
        structured_output_support = STRUCTURED_OUTPUT_CLIENT_VALIDATED
        if isinstance(self.response_format, dict):
            response_type = self.response_format.get("type")
            if response_type == "json_schema":
                structured_output_support = STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA
            elif response_type == "json_object":
                structured_output_support = STRUCTURED_OUTPUT_NATIVE_JSON_OBJECT
        return ProviderCapabilities(
            provider="openai",
            supports_tool_calling=True,
            supports_parallel_tool_calls=bool(self.parallel_tool_calls),
            input_modalities=["text", "image", "audio", "file"],
            supports_tool_media_output=True,
            supports_finalize_streaming=True,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=False,
            structured_output_support=structured_output_support,
            supports_native_async=True,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        request_args = self._base_request_args(messages)
        openai_tools = self._to_openai_tools(tools)
        if openai_tools:
            request_args["tools"] = openai_tools
            request_args["tool_choice"] = self.tool_choice
            request_args["parallel_tool_calls"] = self.parallel_tool_calls
        response, rate_limits = await self._acreate_with_raw_headers(request_args)
        self._last_rate_limits = rate_limits
        return self._action_from_response(response, rate_limits)

    def _planning_stream_args(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> dict[str, Any]:
        args = self._stream_request_args(messages)
        converted = self._to_openai_tools(tools)
        if converted:
            args.update(
                tools=converted,
                tool_choice=self.tool_choice,
                parallel_tool_calls=self.parallel_tool_calls,
            )
        return args

    def _stream_action(self, stream: _ActionStream) -> AgentAction:
        content = "".join(stream.content)
        meta: dict[str, Any] = {"provider": "openai", "model": self.model}
        if stream.usage:
            meta["usage"] = stream.usage
        calls = stream.tool_calls()
        if not calls:
            return AgentAction(response_text=content, metadata=meta)
        payload = openai_like_tool_call_plan_payload(
            provider="openai",
            model=self.model,
            tool_calls=calls,
            content=content,
            tool_call_source="streamed_native_tool_calls",
        )
        return AgentAction(plan=payload["plan"], metadata={**meta, **payload["metadata"]})

    def stream_next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> Iterator[AgentAction | dict[str, Any]]:
        self._last_rate_limits = None
        self._last_stream_usage = None
        chunks = self._client.chat.completions.create(
            **self._planning_stream_args(messages, tools)
        )
        stream = _ActionStream()
        for chunk in chunks:
            text = stream.add(chunk)
            if text:
                yield {"type": "text_chunk", "chunk": text}
        self._last_stream_usage = stream.usage
        yield self._stream_action(stream)

    async def astream_next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AsyncIterator[AgentAction | dict[str, Any]]:
        self._last_rate_limits = None
        self._last_stream_usage = None
        chunks = await self._get_async_client().chat.completions.create(
            **self._planning_stream_args(messages, tools)
        )
        stream = _ActionStream()
        async for chunk in chunks:
            text = stream.add(chunk)
            if text:
                yield {"type": "text_chunk", "chunk": text}
        self._last_stream_usage = stream.usage
        yield self._stream_action(stream)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        del tool_results
        response, rate_limits = await self._acreate_with_raw_headers(
            self._base_request_args(messages)
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        self._last_finalize_rate_limits = rate_limits
        return self._finalize_text_from_response(response)

    async def afinalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> AsyncIterator[str]:
        del tool_results
        self._last_stream_usage = None
        request_args = self._stream_request_args(messages)
        stream = await self._get_async_client().chat.completions.create(**request_args)

        def remember_usage(usage: dict[str, int]) -> None:
            self._last_stream_usage = usage

        async for chunk in aiter_openai_like_stream_content(stream, on_usage=remember_usage):
            yield chunk
