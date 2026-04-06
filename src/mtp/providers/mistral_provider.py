from __future__ import annotations

import asyncio
import json
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    STRUCTURED_OUTPUT_CLIENT_VALIDATED,
    USAGE_METRICS_BASIC,
    calls_to_dependency_batches,
    extract_refs,
    extract_usage_metrics,
    normalize_refs,
    safe_load_arguments,
)
from mistralai.client import Mistral

class MistralToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for Mistral AI.
    """

    def __init__(
        self,
        *,
        model: str = "mistral-large-latest",
        api_key: str | None = None,
        temperature: float = 0.0,
        tool_choice: str = "auto",
        parallel_tool_calls: bool = True,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.parallel_tool_calls = parallel_tool_calls
        self._last_finalize_usage: dict[str, int] | None = None
        self._client = client or self._make_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        key = api_key or require_env("MISTRAL_API_KEY")
        return Mistral(api_key=key)

    def _to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, default=str)
        except Exception:
            return str(content)

    def _to_mistral_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                content = self._to_text(msg.get("content", ""))
                if content.strip():
                    formatted.append({"role": "system", "content": content})
            elif role == "user":
                content = self._to_text(msg.get("content", ""))
                formatted.append({"role": "user", "content": content})
            elif role == "assistant":
                out: dict[str, Any] = {
                    "role": "assistant",
                    "content": self._to_text(msg.get("content", "")),
                }
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    out["tool_calls"] = [
                        {
                            "id": tc.get("id", f"call_{i}"),
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"].get("arguments", "{}"),
                            },
                        }
                        for i, tc in enumerate(tool_calls)
                        if isinstance(tc, dict) and isinstance(tc.get("function"), dict)
                    ]
                formatted.append(out)
            elif role == "tool":
                formatted.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.get("tool_call_id", ""),
                        "content": self._to_text(msg.get("content", "")),
                    }
                )
        return formatted

    def _to_mistral_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
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

    def _extract_mistral_usage(self, response: Any) -> dict[str, int] | None:
        standard = extract_usage_metrics(response)
        if standard:
            return standard
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        result: dict[str, int] = {}
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        total = getattr(usage, "total_tokens", None)
        if prompt is not None:
            result["prompt_tokens"] = int(prompt)
        if completion is not None:
            result["completion_tokens"] = int(completion)
        if total is not None:
            result["total_tokens"] = int(total)
        return result or None

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        mistral_messages = self._to_mistral_messages(messages)
        mistral_tools = self._to_mistral_tools(tools)
        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": mistral_messages,
            "temperature": self.temperature,
        }
        if mistral_tools:
            request_args["tools"] = mistral_tools
            request_args["tool_choice"] = self.tool_choice
        
        response = self._client.chat.complete(**request_args)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)
        usage = self._extract_mistral_usage(response)
        action_meta: dict[str, Any] = {"provider": "mistral", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if tool_calls:
            mtp_calls: list[ToolCall] = []
            id_by_index: dict[int, str] = {}
            serialized_tool_calls: list[dict[str, Any]] = []
            for idx, tc in enumerate(tool_calls):
                call_id = getattr(tc, "id", None) or f"call_{idx}"
                id_by_index[idx] = call_id
                fn = tc.function
                raw_arguments = getattr(fn, "arguments", "{}")
                parsed_args = safe_load_arguments(raw_arguments)
                normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=idx)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                mtp_calls.append(
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
                            "arguments": raw_arguments if isinstance(raw_arguments, str) else json.dumps(raw_arguments),
                        },
                    }
                )
            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "mistral", "model": self.model},
            )
            return AgentAction(
                plan=plan,
                metadata={
                    **action_meta,
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": getattr(message, "content", "") or "",
                        "tool_calls": serialized_tool_calls,
                    },
                },
            )
        response_text = getattr(message, "content", "") or ""
        return AgentAction(response_text=response_text, metadata=action_meta)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        mistral_messages = self._to_mistral_messages(messages)
        response = self._client.chat.complete(
            model=self.model,
            messages=mistral_messages,
            temperature=self.temperature,
        )
        self._last_finalize_usage = self._extract_mistral_usage(response) or None
        message = response.choices[0].message
        return getattr(message, "content", "") or "Done."

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="mistral",
            supports_tool_calling=True,
            supports_parallel_tool_calls=False,
            input_modalities=["text"],
            supports_tool_media_output=False,
            supports_finalize_streaming=False,
            usage_metrics_quality=USAGE_METRICS_BASIC,
            supports_reasoning_metadata=False,
            structured_output_support=STRUCTURED_OUTPUT_CLIENT_VALIDATED,
            supports_native_async=False,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
