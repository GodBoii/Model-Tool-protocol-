from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ToolResult, ToolSpec
from .common import (
    STRUCTURED_OUTPUT_CLIENT_VALIDATED,
    USAGE_METRICS_RICH,
    ProviderCapabilities,
    extract_usage_metrics,
    format_openai_like_message,
    openai_like_tool_call_plan_payload,
)
from .compatible_provider import OpenAICompatibleToolCallingProvider, _AsyncOnlyClient
from .defaults import DEFAULT_PROVIDER_MODELS


class GroqToolCallingProvider(ProviderAdapter):
    def __init__(
        self,
        *,
        model: str = DEFAULT_PROVIDER_MODELS["groq"],
        api_key: str | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        parallel_tool_calls: bool = True,
        encourage_batch_tool_calls: bool = True,
        strict_dependency_mode: bool = False,
        include_reasoning: bool | None = None,
        reasoning_format: str | None = None,
        reasoning_effort: str | None = None,
        stream_include_usage: bool = True,
        max_completion_tokens: int | None = 512,
        client: Any | None = None,
        async_client: Any | None = None,
        native_async: bool = False,
    ) -> None:
        if type(native_async) is not bool:
            raise TypeError("native_async must be a boolean.")
        self._owns_async_client = False
        if native_async and async_client is None:
            from groq import AsyncGroq

            async_client = AsyncGroq(
                api_key=api_key or require_env("GROQ_API_KEY"), timeout=60.0
            )
            self._owns_async_client = True
        self._async_client = async_client
        self._owns_sync_client = client is None and (
            async_client is None or native_async
        )
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.parallel_tool_calls = parallel_tool_calls
        self.encourage_batch_tool_calls = encourage_batch_tool_calls
        self.strict_dependency_mode = strict_dependency_mode
        self.include_reasoning = include_reasoning
        self.reasoning_format = reasoning_format
        self.reasoning_effort = reasoning_effort
        self.stream_include_usage = stream_include_usage
        if max_completion_tokens is not None and (
            type(max_completion_tokens) is not int or max_completion_tokens < 1
        ):
            raise ValueError(
                "max_completion_tokens must be a positive integer or None."
            )
        self.max_completion_tokens = max_completion_tokens
        self._last_response: Any | None = None
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._client = (
            client
            if client is not None
            else (
                self._make_client(api_key=api_key)
                if self._owns_sync_client
                else _AsyncOnlyClient()
            )
        )
        self._native_adapter = (
            _GroqNativeAdapter(self, async_client) if async_client is not None else None
        )

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from groq import Groq
        except Exception as exc:
            raise ImportError(
                "groq is not installed. Install with: pip install groq"
            ) from exc

        key = api_key or require_env("GROQ_API_KEY")
        return Groq(api_key=key, timeout=60.0)

    def _create_completion(self, request_args: dict[str, Any]) -> Any:
        if self.max_completion_tokens is not None:
            request_args.setdefault("max_completion_tokens", self.max_completion_tokens)
        try:
            return self._client.chat.completions.create(**request_args)
        except TypeError:
            request_args.pop("parallel_tool_calls", None)
            request_args.pop("stream_options", None)
            return self._client.chat.completions.create(**request_args)
        except Exception as exc:
            raise RuntimeError(f"Groq API request failed: {exc}") from exc

    @staticmethod
    def _first_choice_message(response: Any) -> Any:
        choices = getattr(response, "choices", None)
        if not choices:
            raise RuntimeError("Groq response did not include any choices.")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise RuntimeError("Groq response choice did not include a message.")
        return message

    def _to_groq_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for tool in tools:
            formatted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema
                        or {"type": "object", "properties": {}},
                    },
                }
            )
        return formatted

    def _to_groq_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        if self.system_prompt:
            formatted.append({"role": "system", "content": self.system_prompt})
        if self.encourage_batch_tool_calls:
            formatted.append(
                {
                    "role": "system",
                    "content": (
                        "When tools are needed, return all independent tool calls in one response. "
                        "Only split into later tool rounds when there is a true dependency on prior tool results."
                    ),
                }
            )
        if self.strict_dependency_mode:
            formatted.append(
                {
                    "role": "system",
                    "content": (
                        "Strict dependency mode enabled. If a tool call depends on a value from an earlier tool call in this same response, "
                        'reference it using a JSON object: {"$ref": <index>}, where <index> is the 0-based position of the tool call you want to use. '
                        'Example: if the first tool call is calculator.add, the second tool can use {"$ref": 0} as an argument.'
                    ),
                }
            )

        for msg in messages:
            converted = format_openai_like_message(
                msg,
                allow_images=True,
                allow_audio=False,
                allow_video=False,
                allow_files=False,
            )
            if converted is None:
                continue
            if converted.get("role") == "tool":
                converted["name"] = msg.get("tool_name") or msg.get("name")
            formatted.append(converted)
        return formatted

    def next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        groq_messages = self._to_groq_messages(messages)
        groq_tools = self._to_groq_tools(tools)

        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": groq_messages,
            "temperature": self.temperature,
        }
        if self.include_reasoning is not None:
            request_args["include_reasoning"] = self.include_reasoning
        if self.reasoning_format is not None:
            request_args["reasoning_format"] = self.reasoning_format
        if self.reasoning_effort is not None:
            request_args["reasoning_effort"] = self.reasoning_effort
        if groq_tools:
            request_args["tools"] = groq_tools
            request_args["tool_choice"] = self.tool_choice
            request_args["parallel_tool_calls"] = (
                self.capabilities().supports_parallel_tool_calls
            )

        response = self._create_completion(request_args)
        self._last_response = response
        message = self._first_choice_message(response)
        tool_calls = getattr(message, "tool_calls", None)
        reasoning = getattr(message, "reasoning", None)
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "groq", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if reasoning:
            action_meta["reasoning"] = reasoning

        content = message.content or ""
        if tool_calls:
            return self._tool_action_from_calls(
                tool_calls=list(tool_calls),
                content=content,
                reasoning=reasoning,
                action_meta=action_meta,
                tool_call_source="native_tool_calls",
            )

        return AgentAction(response_text=content, metadata=action_meta)

    def _tool_action_from_calls(
        self,
        *,
        tool_calls: list[Any],
        content: str,
        reasoning: str | None,
        action_meta: dict[str, Any],
        tool_call_source: str,
    ) -> AgentAction:
        payload = openai_like_tool_call_plan_payload(
            provider="groq",
            model=self.model,
            tool_calls=tool_calls,
            content=content,
            reasoning=reasoning,
            tool_call_source=tool_call_source,
            use_current_index_refs=True,
        )
        return AgentAction(
            plan=payload["plan"],
            metadata={
                **action_meta,
                **payload["metadata"],
            },
        )

    def finalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        groq_messages = self._to_groq_messages(messages)
        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": groq_messages,
            "temperature": self.temperature,
        }
        if self.include_reasoning is not None:
            request_args["include_reasoning"] = self.include_reasoning
        if self.reasoning_format is not None:
            request_args["reasoning_format"] = self.reasoning_format
        if self.reasoning_effort is not None:
            request_args["reasoning_effort"] = self.reasoning_effort
        response = self._create_completion(request_args)
        self._last_response = response
        self._last_finalize_usage = extract_usage_metrics(response) or None
        message = self._first_choice_message(response)
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; multi-round chaining is next on roadmap."
        return message.content or "Done."

    def finalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> Iterator[str]:
        groq_messages = self._to_groq_messages(messages)
        self._last_stream_usage = None
        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": groq_messages,
            "temperature": self.temperature,
            "stream": True,
            "stream_options": {"include_usage": self.stream_include_usage},
        }
        if self.include_reasoning is not None:
            request_args["include_reasoning"] = self.include_reasoning
        if self.reasoning_format is not None:
            request_args["reasoning_format"] = self.reasoning_format
        if self.reasoning_effort is not None:
            request_args["reasoning_effort"] = self.reasoning_effort
        stream = self._create_completion(request_args)
        for chunk in stream:
            chunk_usage = extract_usage_metrics(chunk)
            if chunk_usage:
                self._last_stream_usage = chunk_usage
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield content

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="groq",
            supports_tool_calling=True,
            supports_parallel_tool_calls=bool(self.parallel_tool_calls)
            and not self.model.startswith("openai/gpt-oss"),
            input_modalities=["text", "image"]
            if "vision" in self.model or "llama-4" in self.model
            else ["text"],
            supports_tool_media_output=True,
            supports_finalize_streaming=True,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=True,
            structured_output_support=STRUCTURED_OUTPUT_CLIENT_VALIDATED,
            supports_native_async=self._async_client is not None,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        if self._native_adapter is not None:
            return await self._native_adapter.anext_action(messages, tools)
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        if self._native_adapter is not None:
            result = await self._native_adapter.afinalize(messages, tool_results)
            self._last_finalize_usage = self._native_adapter._last_finalize_usage
            self._last_finalize_message = self._native_adapter._last_finalize_message
            return result
        return await asyncio.to_thread(self.finalize, messages, tool_results)

    async def astream_next_action(self, messages, tools):
        if self._native_adapter is None:
            yield await asyncio.to_thread(self.next_action, messages, tools)
            return
        iterator = self._native_adapter.astream_next_action(messages, tools)
        try:
            async for item in iterator:
                yield item
        finally:
            await iterator.aclose()

    async def afinalize_stream(self, messages, tool_results):
        if self._native_adapter is None:
            from ..async_stream import async_from_sync

            async for chunk in async_from_sync(
                lambda: self.finalize_stream(messages, tool_results)
            ):
                yield chunk
            return
        iterator = self._native_adapter.afinalize_stream(messages, tool_results)
        try:
            async for chunk in iterator:
                yield chunk
            self._last_stream_usage = self._native_adapter._last_stream_usage
            self._last_finalize_message = self._native_adapter._last_finalize_message
        finally:
            await iterator.aclose()

    async def aclose(self):
        if self._owns_async_client:
            await self._async_client.close()
        if self._owns_sync_client:
            self._client.close()


class _GroqNativeAdapter(OpenAICompatibleToolCallingProvider):
    """Groq-specific requests with the shared validated native async parser."""

    def __init__(self, owner, async_client):
        self.owner = owner
        super().__init__(
            model=owner.model,
            base_url="https://api.groq.com/openai/v1",
            async_client=async_client,
            provider_name="groq",
            max_tokens=None,
            temperature=owner.temperature,
            parallel_tool_calls=owner.parallel_tool_calls,
            tool_choice=owner.tool_choice,
            stream_include_usage=owner.stream_include_usage,
            input_modalities=tuple(owner.capabilities().input_modalities),
        )

    def _to_openai_messages(self, messages):
        return super()._to_openai_messages(self.owner._to_groq_messages(messages))

    def _request(self, messages, tools, *, stream=False):
        request = super()._request(messages, tools, stream=stream)
        # AsyncGroq emits usage through x_groq and has no stream_options argument.
        request.pop("stream_options", None)
        if self.owner.max_completion_tokens is not None:
            request["max_completion_tokens"] = self.owner.max_completion_tokens
        if self.owner.model.startswith("openai/gpt-oss"):
            request.pop("parallel_tool_calls", None)
        for name in ("include_reasoning", "reasoning_format", "reasoning_effort"):
            value = getattr(self.owner, name)
            if value is not None:
                request[name] = value
        return request
