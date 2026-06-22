from __future__ import annotations

import asyncio
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    USAGE_METRICS_RICH,
    STRUCTURED_OUTPUT_CLIENT_VALIDATED,
    calls_to_dependency_batches,
    extract_refs,
    extract_usage_metrics,
    format_openai_like_message,
    openai_like_tool_call_plan_payload,
)


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
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        parallel_tool_calls: bool = True,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.parallel_tool_calls = parallel_tool_calls
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_rate_limits: dict[str, Any] | None = None
        self._last_finalize_rate_limits: dict[str, Any] | None = None
        self._client = client or self._make_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. Please install using `pip install openai`"
            ) from exc

        key = api_key or require_env("OPENAI_API_KEY")
        return OpenAI(api_key=key)

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

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        openai_messages = self._to_openai_messages(messages)
        openai_tools = self._to_openai_tools(tools)

        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": self.temperature,
        }
        if openai_tools:
            request_args["tools"] = openai_tools
            request_args["tool_choice"] = self.tool_choice
            request_args["parallel_tool_calls"] = self.parallel_tool_calls

        response, rate_limits = self._create_with_raw_headers(request_args)
        self._last_rate_limits = rate_limits
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

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        openai_messages = self._to_openai_messages(messages)
        response, rate_limits = self._create_with_raw_headers(
            {
                "model": self.model,
                "messages": openai_messages,
                "temperature": self.temperature,
            }
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        self._last_finalize_rate_limits = rate_limits
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="openai",
            supports_tool_calling=True,
            supports_parallel_tool_calls=bool(self.parallel_tool_calls),
            input_modalities=["text", "image", "audio", "file"],
            supports_tool_media_output=True,
            supports_finalize_streaming=False,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=False,
            structured_output_support=STRUCTURED_OUTPUT_CLIENT_VALIDATED,
            supports_native_async=False,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
