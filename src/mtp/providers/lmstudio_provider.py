from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    STRUCTURED_OUTPUT_CLIENT_VALIDATED,
    USAGE_METRICS_RICH,
    calls_to_dependency_batches,
    extract_refs,
    extract_usage_metrics,
    format_openai_like_message,
    normalize_refs,
    safe_load_arguments,
)


class LMStudioToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for LM Studio's local OpenAI-compatible server.

    By default LM Studio serves an OpenAI-compatible API at:
    http://127.0.0.1:1234/v1

    Authentication is usually not required for local use. The OpenAI client
    still expects an api_key field, so this adapter supplies a harmless default
    token when one is not provided.
    """

    def __init__(
        self,
        *,
        model: str = "qwen3",
        base_url: str = "http://127.0.0.1:1234/v1",
        api_key: str | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        parallel_tool_calls: bool = True,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.parallel_tool_calls = parallel_tool_calls
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._client = client or self._make_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. LM Studio uses the OpenAI-compatible API. "
                "Install with: pip install openai"
            ) from exc

        key = api_key or os.getenv("LMSTUDIO_API_KEY") or "lm-studio"
        return OpenAI(base_url=self.base_url, api_key=key)

    def _to_lmstudio_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            converted = format_openai_like_message(
                msg,
                allow_images=True,
                allow_audio=False,
                allow_video=False,
                allow_files=False,
            )
            if converted is not None:
                formatted.append(converted)
        return formatted

    def _to_lmstudio_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
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
        lmstudio_messages = self._to_lmstudio_messages(messages)
        lmstudio_tools = self._to_lmstudio_tools(tools)

        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": lmstudio_messages,
            "temperature": self.temperature,
        }
        if lmstudio_tools:
            request_args["tools"] = lmstudio_tools
            request_args["tool_choice"] = self.tool_choice
            request_args["parallel_tool_calls"] = self.parallel_tool_calls

        try:
            response = self._client.chat.completions.create(**request_args)
        except TypeError:
            request_args.pop("parallel_tool_calls", None)
            response = self._client.chat.completions.create(**request_args)

        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "lmstudio", "model": self.model}
        if usage:
            action_meta["usage"] = usage

        if tool_calls:
            mtp_calls: list[ToolCall] = []
            id_by_index: dict[int, str] = {}
            serialized_tool_calls: list[dict[str, Any]] = []
            call_reasoning = message.content.strip() if isinstance(message.content, str) and message.content.strip() else None
            for idx, tc in enumerate(tool_calls):
                call_id = tc.id or f"call_{idx}"
                id_by_index[idx] = call_id
                parsed_args = safe_load_arguments(tc.function.arguments)
                normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=idx)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                mtp_calls.append(
                    ToolCall(
                        id=call_id,
                        name=tc.function.name,
                        arguments=normalized_args,
                        depends_on=depends_on,
                        reasoning=call_reasoning,
                    )
                )
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                        "reasoning": call_reasoning,
                    }
                )

            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "lmstudio", "model": self.model},
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

    def stream_next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> Iterator[AgentAction | dict[str, Any]]:
        lmstudio_messages = self._to_lmstudio_messages(messages)
        lmstudio_tools = self._to_lmstudio_tools(tools)

        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": lmstudio_messages,
            "temperature": self.temperature,
            "stream": True,
        }
        if lmstudio_tools:
            request_args["tools"] = lmstudio_tools
            request_args["tool_choice"] = self.tool_choice
            request_args["parallel_tool_calls"] = self.parallel_tool_calls

        try:
            stream = self._client.chat.completions.create(**request_args)
        except TypeError:
            request_args.pop("parallel_tool_calls", None)
            stream = self._client.chat.completions.create(**request_args)

        content_acc = ""
        reasoning_acc = ""
        usage = None
        tool_calls_dict: dict[int, Any] = {}

        for chunk in stream:
            chunk_usage = extract_usage_metrics(chunk)
            if chunk_usage:
                usage = chunk_usage

            if not getattr(chunk, "choices", None):
                continue

            delta = chunk.choices[0].delta
            
            chunk_reasoning = getattr(delta, "reasoning_content", None)
            chunk_content = getattr(delta, "content", None)

            if chunk_reasoning and isinstance(chunk_reasoning, str):
                yield {"type": "reasoning_chunk", "chunk": chunk_reasoning}
                reasoning_acc += chunk_reasoning

            if chunk_content and isinstance(chunk_content, str):
                yield {"type": "text_chunk", "chunk": chunk_content}
                content_acc += chunk_content

            chunk_tool_calls = getattr(delta, "tool_calls", None)
            if chunk_tool_calls:
                for tc in chunk_tool_calls:
                    index = tc.index
                    if index not in tool_calls_dict:
                        tool_calls_dict[index] = {
                            "id": tc.id, 
                            "type": "function", 
                            "function": {"name": getattr(tc.function, "name", ""), "arguments": getattr(tc.function, "arguments", "") or ""}
                        }
                    else:
                        if getattr(tc.function, "arguments", None):
                            tool_calls_dict[index]["function"]["arguments"] += tc.function.arguments

        action_meta: dict[str, Any] = {"provider": "lmstudio", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if reasoning_acc.strip():
            action_meta["reasoning"] = reasoning_acc.strip()

        if tool_calls_dict:
            mtp_calls: list[ToolCall] = []
            id_by_index: dict[int, str] = {}
            serialized_tool_calls: list[dict[str, Any]] = []
            call_reasoning = reasoning_acc.strip() if reasoning_acc.strip() else content_acc.strip() if content_acc.strip() else None
            
            for idx, tc in tool_calls_dict.items():
                call_id = tc["id"] or f"call_{idx}"
                id_by_index[idx] = call_id
                args_str = tc["function"]["arguments"]
                parsed_args = safe_load_arguments(args_str)
                normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=idx)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                tool_name = tc["function"]["name"]
                
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
                        "function": {"name": tool_name, "arguments": args_str},
                        "reasoning": call_reasoning,
                    }
                )

            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "lmstudio", "model": self.model},
            )
            yield AgentAction(
                plan=plan,
                metadata={
                    **action_meta,
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": content_acc,
                        "tool_calls": serialized_tool_calls,
                        "reasoning": reasoning_acc,
                    },
                },
            )
            return

        yield AgentAction(response_text=content_acc, metadata=action_meta)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        lmstudio_messages = self._to_lmstudio_messages(messages)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=lmstudio_messages,
            temperature=self.temperature,
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    def finalize_stream(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> Iterator[str]:
        lmstudio_messages = self._to_lmstudio_messages(messages)
        self._last_stream_usage = None
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=lmstudio_messages,
            temperature=self.temperature,
            stream=True,
        )
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
            provider="lmstudio",
            supports_tool_calling=True,
            supports_parallel_tool_calls=bool(self.parallel_tool_calls),
            input_modalities=["text", "image"],
            supports_tool_media_output=True,
            supports_finalize_streaming=True,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=False,
            structured_output_support=STRUCTURED_OUTPUT_CLIENT_VALIDATED,
            supports_native_async=False,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def astream_next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> Any:
        stream = self.stream_next_action(messages, tools)
        while True:
            try:
                chunk = await asyncio.to_thread(next, stream)
                yield chunk
            except StopIteration:
                break

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
