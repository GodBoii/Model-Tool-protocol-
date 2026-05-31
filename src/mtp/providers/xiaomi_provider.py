from __future__ import annotations

import asyncio
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


class XiaomiToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for Xiaomi MiMo's OpenAI-compatible API.

    Xiaomi's Token Plan endpoint accepts standard OpenAI chat completion
    requests at https://token-plan-ams.xiaomimimo.com/v1.
    """

    def __init__(
        self,
        *,
        model: str = "mimo-v2.5-pro",
        api_key: str | None = None,
        base_url: str = "https://token-plan-ams.xiaomimimo.com/v1",
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        parallel_tool_calls: bool = True,
        thinking_mode: str = "adaptive",
        final_thinking_mode: str | None = "enabled",
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.parallel_tool_calls = parallel_tool_calls
        self.thinking_mode = self._normalize_thinking_mode(thinking_mode, field_name="thinking_mode")
        self.final_thinking_mode = self._normalize_thinking_mode(
            final_thinking_mode,
            field_name="final_thinking_mode",
            allow_none=True,
        )
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._last_finalize_message: dict[str, Any] | None = None
        self._last_finalize_reasoning: str | None = None
        self._last_stream_reasoning: str | None = None
        self._client = client or self._make_client(api_key=api_key)

    @staticmethod
    def _normalize_thinking_mode(
        value: str | None,
        *,
        field_name: str,
        allow_none: bool = False,
    ) -> str | None:
        if value is None:
            if allow_none:
                return None
            raise ValueError(f"{field_name} cannot be None")
        normalized = str(value).strip().lower()
        if normalized in {"", "default", "auto"}:
            normalized = "adaptive"
        if allow_none and normalized in {"off", "none"}:
            return None
        if normalized not in {"adaptive", "enabled", "disabled"}:
            raise ValueError(f"{field_name} must be one of: adaptive, enabled, disabled")
        return normalized

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. Xiaomi MiMo uses the OpenAI-compatible API. "
                "Install with: pip install openai"
            ) from exc

        key = api_key or require_env("MIMO_API_KEY")
        return OpenAI(base_url=self.base_url, api_key=key, timeout=60.0)

    def _to_xiaomi_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                reasoning_content = msg.get("reasoning_content")
                if isinstance(reasoning_content, str) and reasoning_content.strip():
                    converted["reasoning_content"] = reasoning_content
                formatted.append(converted)
        return formatted

    def _to_xiaomi_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
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

    def _history_contains_tool_round(self, messages: list[dict[str, Any]]) -> bool:
        for msg in messages:
            role = msg.get("role")
            if role == "tool":
                return True
            if role == "assistant" and isinstance(msg.get("tool_calls"), list) and msg.get("tool_calls"):
                return True
        return False

    def _resolve_thinking_type(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None,
        *,
        stage: str,
    ) -> str | None:
        if stage == "finalize":
            if self.final_thinking_mode is not None:
                return self.final_thinking_mode
            return "disabled" if self.thinking_mode == "disabled" else "enabled"
        if not tools:
            return "disabled" if self.thinking_mode == "disabled" else "enabled"
        if self.thinking_mode == "disabled":
            return "disabled"
        return "disabled" if self._history_contains_tool_round(messages) else "enabled"


    def _request_args(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None = None,
        *,
        stream: bool = False,
        stage: str = "plan",
    ) -> dict[str, Any]:
        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_xiaomi_messages(messages),
            "temperature": self.temperature,
        }
        openai_tools = self._to_xiaomi_tools(tools or [])
        if openai_tools:
            request_args["tools"] = openai_tools
            request_args["tool_choice"] = self.tool_choice
            request_args["parallel_tool_calls"] = self.parallel_tool_calls
        thinking_type = self._resolve_thinking_type(messages, tools, stage=stage)
        if thinking_type is not None:
            request_args["extra_body"] = {"thinking": {"type": thinking_type}}
        if stream:
            request_args["stream"] = True
            request_args["stream_options"] = {"include_usage": True}
        return request_args

    def _create_completion(self, request_args: dict[str, Any]) -> Any:
        try:
            return self._client.chat.completions.create(**request_args)
        except TypeError:
            request_args.pop("parallel_tool_calls", None)
            return self._client.chat.completions.create(**request_args)

    @staticmethod
    def _first_choice_message(response: Any) -> Any:
        choices = getattr(response, "choices", None)
        if not choices:
            raise RuntimeError("Xiaomi response did not include any choices.")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise RuntimeError("Xiaomi response choice did not include a message.")
        return message

    @staticmethod
    def _extract_reasoning(message: Any) -> str | None:
        reasoning = getattr(message, "reasoning_content", None)
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning.strip()
        reasoning = getattr(message, "reasoning", None)
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning.strip()
        return None

    @staticmethod
    def _assistant_message(
        *,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning: str | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        if reasoning:
            message["reasoning"] = reasoning
            message["reasoning_content"] = reasoning
        return message

    def _tool_action_from_calls(
        self,
        *,
        tool_calls: list[dict[str, Any]],
        content: str,
        reasoning: str | None,
        usage: dict[str, int] | None,
        tool_call_source: str,
    ) -> AgentAction:
        action_meta: dict[str, Any] = {"provider": "xiaomi", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if reasoning:
            action_meta["reasoning"] = reasoning

        mtp_calls: list[ToolCall] = []
        id_by_index: dict[int, str] = {}
        serialized_tool_calls: list[dict[str, Any]] = []
        call_reasoning = reasoning.strip() if isinstance(reasoning, str) and reasoning.strip() else None

        for idx, tc in enumerate(tool_calls):
            if isinstance(tc, dict):
                call_id = str(tc.get("id") or f"call_{idx}")
                function = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                tool_name = str(function.get("name") or tc.get("name") or "")
                raw_arguments = function.get("arguments") or tc.get("arguments") or "{}"
            else:
                function = getattr(tc, "function", None)
                call_id = getattr(tc, "id", None) or f"call_{idx}"
                tool_name = getattr(function, "name", "")
                raw_arguments = getattr(function, "arguments", None) or "{}"
            if not tool_name:
                raise RuntimeError(f"Xiaomi tool call {call_id!r} is missing a function name.")
            id_by_index[idx] = call_id
            parsed_args = safe_load_arguments(raw_arguments)
            if isinstance(tc, dict) and isinstance(tc.get("arguments"), dict):
                parsed_args = tc["arguments"]
            normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=idx)
            depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
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
                    "function": {
                        "name": tool_name,
                        "arguments": raw_arguments if isinstance(raw_arguments, str) else "{}",
                    },
                    "reasoning": call_reasoning,
                }
            )

        plan = ExecutionPlan(
            batches=calls_to_dependency_batches(mtp_calls),
            metadata={"provider": "xiaomi", "model": self.model},
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
                "assistant_tool_message": self._assistant_message(
                    content=content,
                    tool_calls=serialized_tool_calls,
                    reasoning=reasoning,
                ),
            },
        )

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        response = self._create_completion(self._request_args(messages, tools, stage="plan"))
        message = self._first_choice_message(response)
        tool_calls = getattr(message, "tool_calls", None)
        reasoning = self._extract_reasoning(message)
        usage = extract_usage_metrics(response)
        content = message.content or ""

        if tool_calls:
            return self._tool_action_from_calls(
                tool_calls=list(tool_calls),
                content=content,
                reasoning=reasoning,
                usage=usage or None,
                tool_call_source="native_tool_calls",
            )

        action_meta: dict[str, Any] = {"provider": "xiaomi", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if reasoning:
            action_meta["reasoning"] = reasoning
        action_meta["assistant_message"] = self._assistant_message(content=content, reasoning=reasoning)
        return AgentAction(response_text=content, metadata=action_meta)

    def stream_next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> Iterator[AgentAction | dict[str, Any]]:
        stream = self._create_completion(self._request_args(messages, tools, stream=True, stage="plan"))

        content_acc = ""
        reasoning_acc = ""
        usage = None
        tool_calls_dict: dict[int, dict[str, Any]] = {}

        for chunk in stream:
            chunk_usage = extract_usage_metrics(chunk)
            if chunk_usage:
                usage = chunk_usage

            if not getattr(chunk, "choices", None):
                continue

            delta = chunk.choices[0].delta
            chunk_reasoning = getattr(delta, "reasoning_content", None)
            chunk_content = getattr(delta, "content", None)

            if isinstance(chunk_reasoning, str) and chunk_reasoning:
                reasoning_acc += chunk_reasoning
                yield {"type": "reasoning_chunk", "chunk": chunk_reasoning}

            if isinstance(chunk_content, str) and chunk_content:
                content_acc += chunk_content

            chunk_tool_calls = getattr(delta, "tool_calls", None)
            if chunk_tool_calls:
                for tc in chunk_tool_calls:
                    index = getattr(tc, "index", 0)
                    entry = tool_calls_dict.setdefault(
                        index,
                        {
                            "id": getattr(tc, "id", None),
                            "function": {
                                "name": getattr(getattr(tc, "function", None), "name", "") or "",
                                "arguments": "",
                            },
                        },
                    )
                    if getattr(tc, "id", None):
                        entry["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None and getattr(fn, "name", None):
                        entry["function"]["name"] = fn.name
                    if fn is not None and getattr(fn, "arguments", None):
                        entry["function"]["arguments"] += fn.arguments

        reasoning = reasoning_acc.strip() or None
        if tool_calls_dict:
            ordered_tool_calls = [tool_calls_dict[idx] for idx in sorted(tool_calls_dict)]
            yield self._tool_action_from_calls(
                tool_calls=ordered_tool_calls,
                content=content_acc,
                reasoning=reasoning,
                usage=usage,
                tool_call_source="native_tool_calls",
            )
            return

        action_meta: dict[str, Any] = {"provider": "xiaomi", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if reasoning:
            action_meta["reasoning"] = reasoning
        action_meta["assistant_message"] = self._assistant_message(content=content_acc, reasoning=reasoning)
        if content_acc:
            yield {"type": "text_chunk", "chunk": content_acc}
        yield AgentAction(response_text=content_acc, metadata=action_meta)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        response = self._create_completion(self._request_args(messages, stage="finalize"))
        self._last_finalize_usage = extract_usage_metrics(response) or None
        message = self._first_choice_message(response)
        reasoning = self._extract_reasoning(message)
        self._last_finalize_reasoning = reasoning
        self._last_finalize_message = self._assistant_message(content=message.content or "Done.", reasoning=reasoning)
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    def finalize_stream(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> Iterator[str]:
        request_args = self._request_args(messages, stream=True, stage="finalize")
        self._last_stream_usage = None
        self._last_stream_reasoning = None
        stream = self._create_completion(request_args)
        final_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        for chunk in stream:
            chunk_usage = extract_usage_metrics(chunk)
            if chunk_usage:
                self._last_stream_usage = chunk_usage
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if isinstance(reasoning, str) and reasoning:
                reasoning_chunks.append(reasoning)
            content = getattr(delta, "content", None)
            if content:
                final_chunks.append(content)
                yield content
        combined_reasoning = "".join(reasoning_chunks).strip() or None
        self._last_stream_reasoning = combined_reasoning
        self._last_finalize_reasoning = combined_reasoning
        self._last_finalize_message = self._assistant_message(
            content="".join(final_chunks) or "Done.",
            reasoning=combined_reasoning,
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="xiaomi",
            supports_tool_calling=True,
            supports_parallel_tool_calls=bool(self.parallel_tool_calls),
            input_modalities=["text", "image", "audio", "file"],
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

    @staticmethod
    def _next_stream_item(stream: Iterator[AgentAction | dict[str, Any]]) -> AgentAction | dict[str, Any] | None:
        try:
            return next(stream)
        except StopIteration:
            return None

    async def astream_next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> Any:
        stream = self.stream_next_action(messages, tools)
        while True:
            chunk = await asyncio.to_thread(self._next_stream_item, stream)
            if chunk is None:
                break
            yield chunk

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
