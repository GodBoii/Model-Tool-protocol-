from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
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


class GroqToolCallingProvider(ProviderAdapter):
    def __init__(
        self,
        *,
        model: str = "llama-3.3-70b-versatile",
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
        client: Any | None = None,
    ) -> None:
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
        self._last_response: Any | None = None
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._client = client or self._make_client(api_key=api_key)

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
                        "parameters": tool.input_schema or {"type": "object", "properties": {}},
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
                        "reference it using a JSON object: {\"$ref\": <index>}, where <index> is the 0-based position of the tool call you want to use. "
                        "Example: if the first tool call is calculator.add, the second tool can use {\"$ref\": 0} as an argument."
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

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
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
            request_args["parallel_tool_calls"] = self.parallel_tool_calls

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
                fn_name = getattr(function, "name", "")
                raw_args = getattr(function, "arguments", None) or "{}"
                parsed_args = None
                call_id = getattr(tc, "id", None) or f"call_{idx}"
            if not fn_name:
                raise RuntimeError(f"Groq tool call {call_id!r} is missing a function name.")
            id_by_index[idx] = call_id
            if parsed_args is None:
                parsed_args = safe_load_arguments(raw_args)
            parsed_calls.append((idx, call_id, fn_name, parsed_args, raw_args))
            serialized_tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": fn_name, "arguments": raw_args},
                    "reasoning": call_reasoning,
                }
            )

        for idx, call_id, fn_name, parsed_args, _raw_args in parsed_calls:
            normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=idx)
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
            metadata={"provider": "groq", "model": self.model},
        )
        derived_batch_modes = [batch.mode for batch in plan.batches]
        return AgentAction(
            plan=plan,
            metadata={
                **action_meta,
                "tool_call_source": tool_call_source,
                "raw_tool_call_count": len(tool_calls),
                "derived_batch_count": len(plan.batches),
                "derived_batch_modes": derived_batch_modes,
                "assistant_tool_message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": serialized_tool_calls,
                    "reasoning": reasoning,
                },
            },
        )

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
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

    def finalize_stream(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> Iterator[str]:
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
            supports_parallel_tool_calls=bool(self.parallel_tool_calls),
            input_modalities=["text", "image"],
            supports_tool_media_output=True,
            supports_finalize_streaming=True,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=True,
            structured_output_support=STRUCTURED_OUTPUT_CLIENT_VALIDATED,
            supports_native_async=False,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
