from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult, ToolSpec
from .common import extract_usage_metrics


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

    def _extract_refs(self, value: Any) -> list[str]:
        refs: list[str] = []
        if isinstance(value, dict):
            if "$ref" in value and isinstance(value["$ref"], str):
                refs.append(value["$ref"])
            for v in value.values():
                refs.extend(self._extract_refs(v))
            return refs
        if isinstance(value, list):
            for v in value:
                refs.extend(self._extract_refs(v))
        return refs

    def _calls_to_dependency_batches(self, calls: list[ToolCall]) -> list[ToolBatch]:
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
                # Fallback for malformed/cyclic dependency sets; preserve deterministic execution.
                unresolved_calls = [remaining[call_id] for call_id in ordered_ids if call_id in remaining]
                batches.append(ToolBatch(mode="sequential", calls=unresolved_calls))
                break

            ready_calls = [remaining.pop(call_id) for call_id in ready_ids]
            mode = "parallel" if len(ready_calls) > 1 else "sequential"
            batches.append(ToolBatch(mode=mode, calls=ready_calls))
            done.update(ready_ids)

        return batches

    def _normalize_refs(self, value: Any, id_by_index: dict[int, str]) -> Any:
        if isinstance(value, dict):
            normalized = {}
            for k, v in value.items():
                if k == "$ref":
                    ref_value = v
                    if isinstance(ref_value, int) and ref_value in id_by_index:
                        normalized[k] = id_by_index[ref_value]
                    elif isinstance(ref_value, str) and ref_value.isdigit():
                        idx = int(ref_value)
                        normalized[k] = id_by_index.get(idx, ref_value)
                    elif isinstance(ref_value, str):
                        match = re.search(r"(\d+)$", ref_value)
                        if match:
                            idx = int(match.group(1))
                            normalized[k] = id_by_index.get(idx, ref_value)
                        else:
                            normalized[k] = ref_value
                    else:
                        normalized[k] = ref_value
                else:
                    normalized[k] = self._normalize_refs(v, id_by_index)
            return normalized
        if isinstance(value, list):
            return [self._normalize_refs(v, id_by_index) for v in value]
        return value

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from groq import Groq
        except Exception as exc:
            raise ImportError(
                "groq is not installed. Install with: pip install groq"
            ) from exc

        key = api_key or require_env("GROQ_API_KEY")
        return Groq(api_key=key)

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
                        "Strict dependency mode: if one tool call depends on another tool output, "
                        "pass that value using a JSON ref object like {\"$ref\":\"<tool_call_id>\"} "
                        "inside arguments. Do not hardcode derived intermediate values."
                    ),
                }
            )

        for msg in messages:
            role = msg.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                continue

            if role == "tool":
                content = msg.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content)
                formatted.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.get("tool_call_id"),
                        "name": msg.get("tool_name") or msg.get("name"),
                        "content": content,
                    }
                )
                continue

            assistant_msg: dict[str, Any] = {"role": role}
            if "tool_calls" in msg:
                assistant_msg["tool_calls"] = msg["tool_calls"]
                assistant_msg["content"] = msg.get("content") or ""
            else:
                assistant_msg["content"] = msg.get("content") or ""
            formatted.append(assistant_msg)
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

        try:
            response = self._client.chat.completions.create(**request_args)
        except TypeError:
            # Backward compatibility for clients/models that don't support parallel_tool_calls param.
            request_args.pop("parallel_tool_calls", None)
            response = self._client.chat.completions.create(**request_args)
        self._last_response = response
        choice = response.choices[0]
        message = choice.message
        tool_calls = getattr(message, "tool_calls", None)
        reasoning = getattr(message, "reasoning", None)
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "groq", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if reasoning:
            action_meta["reasoning"] = reasoning

        if tool_calls:
            mtp_calls: list[ToolCall] = []
            serialized_tool_calls: list[dict[str, Any]] = []
            parsed_calls: list[tuple[int, str, str, dict[str, Any]]] = []
            id_by_index: dict[int, str] = {}

            for idx, tc in enumerate(tool_calls):
                fn_name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                call_id = tc.id or f"call_{idx}"
                id_by_index[idx] = call_id
                try:
                    parsed_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    parsed_args = {"_raw_arguments": raw_args}
                parsed_calls.append((idx, call_id, fn_name, parsed_args))
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": fn_name, "arguments": raw_args},
                    }
                )

            for idx, call_id, fn_name, parsed_args in parsed_calls:
                _ = idx
                normalized_args = self._normalize_refs(parsed_args, id_by_index)
                depends_on = list(dict.fromkeys(self._extract_refs(normalized_args)))
                mtp_calls.append(
                    ToolCall(
                        id=call_id,
                        name=fn_name,
                        arguments=normalized_args,
                        depends_on=depends_on,
                    )
                )

            plan = ExecutionPlan(
                batches=self._calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "groq", "model": self.model},
            )
            return AgentAction(
                plan=plan,
                metadata={
                    **action_meta,
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": serialized_tool_calls,
                        "reasoning": reasoning,
                    },
                },
            )

        return AgentAction(response_text=message.content or "", metadata=action_meta)

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
        response = self._client.chat.completions.create(**request_args)
        self._last_response = response
        self._last_finalize_usage = extract_usage_metrics(response) or None
        choice = response.choices[0]
        message = choice.message
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
        try:
            stream = self._client.chat.completions.create(**request_args)
        except TypeError:
            request_args.pop("stream_options", None)
            stream = self._client.chat.completions.create(**request_args)
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

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
