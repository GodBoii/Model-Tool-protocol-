from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    STRUCTURED_OUTPUT_CLIENT_VALIDATED,
    USAGE_METRICS_RICH,
    extract_usage_metrics,
    aiter_openai_like_stream_content,
    format_openai_like_message,
    iter_openai_like_stream_content,
    openai_like_tool_call_plan_payload,
)


class SambaNovaToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for SambaNova Cloud.
    Ultra-fast inference for Llama models.
    """

    def __init__(
        self,
        *,
        model: str = "Meta-Llama-3.1-70B-Instruct",
        api_key: str | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        client: Any | None = None,
        async_client: Any | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.tool_choice = tool_choice
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._client = client or self._make_client(api_key=api_key)
        self._async_client = async_client
        if self._async_client is None and client is None:
            self._async_client = self._make_async_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. Please install using `pip install openai`"
            ) from exc

        key = api_key or require_env("SAMBANOVA_API_KEY")
        return OpenAI(
            base_url="https://api.sambanova.ai/v1",
            api_key=key,
        )

    def _make_async_client(self, api_key: str | None) -> Any:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. Please install using `pip install openai`"
            ) from exc

        key = api_key or require_env("SAMBANOVA_API_KEY")
        return AsyncOpenAI(base_url="https://api.sambanova.ai/v1", api_key=key)

    async def _acreate_completion(self, request_args: dict[str, Any]) -> Any:
        if self._async_client is None:
            return await asyncio.to_thread(
                self._client.chat.completions.create, **request_args
            )
        try:
            return await self._async_client.chat.completions.create(**request_args)
        except TypeError:
            compatible_args = dict(request_args)
            compatible_args.pop("stream_options", None)
            return await self._async_client.chat.completions.create(**compatible_args)

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

        response = self._client.chat.completions.create(**request_args)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "sambanova", "model": self.model}
        if usage:
            action_meta["usage"] = usage

        if tool_calls:
            payload = openai_like_tool_call_plan_payload(
                provider="sambanova",
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
        response = self._client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            temperature=self.temperature,
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    def finalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> Iterator[str]:
        self._last_stream_usage = None
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=self._to_openai_messages(messages),
            temperature=self.temperature,
            stream=True,
            stream_options={"include_usage": True},
        )

        def remember_usage(usage: dict[str, int]) -> None:
            self._last_stream_usage = usage

        yield from iter_openai_like_stream_content(stream, on_usage=remember_usage)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="sambanova",
            supports_tool_calling=True,
            supports_parallel_tool_calls=False,
            input_modalities=["text", "image", "audio", "file"],
            supports_tool_media_output=True,
            supports_finalize_streaming=True,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=False,
            structured_output_support=STRUCTURED_OUTPUT_CLIENT_VALIDATED,
            supports_native_async=self._async_client is not None,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        if self._async_client is None:
            return await asyncio.to_thread(self.next_action, messages, tools)
        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "temperature": self.temperature,
        }
        tools_payload = self._to_openai_tools(tools)
        if tools_payload:
            request_args.update(tools=tools_payload, tool_choice=self.tool_choice)
        response = await self._acreate_completion(request_args)
        message = response.choices[0].message
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "sambanova", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            payload = openai_like_tool_call_plan_payload(
                provider="sambanova",
                model=self.model,
                tool_calls=list(tool_calls),
                content=message.content or "",
                tool_call_source="native_tool_calls",
            )
            return AgentAction(
                plan=payload["plan"], metadata={**action_meta, **payload["metadata"]}
            )
        return AgentAction(response_text=message.content or "", metadata=action_meta)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        if self._async_client is None:
            return await asyncio.to_thread(self.finalize, messages, tool_results)
        response = await self._acreate_completion(
            {
                "model": self.model,
                "messages": self._to_openai_messages(messages),
                "temperature": self.temperature,
            }
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    async def afinalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> AsyncIterator[str]:
        if self._async_client is None:
            for chunk in self.finalize_stream(messages, tool_results):
                yield chunk
            return
        self._last_stream_usage = None
        stream = await self._acreate_completion(
            {
                "model": self.model,
                "messages": self._to_openai_messages(messages),
                "temperature": self.temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
        )

        def remember_usage(usage: dict[str, int]) -> None:
            self._last_stream_usage = usage

        async for chunk in aiter_openai_like_stream_content(stream, on_usage=remember_usage):
            yield chunk
