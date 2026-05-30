from __future__ import annotations

import asyncio
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


class DeepSeekToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for DeepSeek.

    DeepSeek offers two flagship models:
        - deepseek-chat   : DeepSeek-V3, best general-purpose + tool calling (default)
        - deepseek-reasoner : DeepSeek-R1, chain-of-thought reasoning model

    The API is OpenAI-compatible. R1 exposes its reasoning trace via
    ``reasoning_content`` on the response message, which MTP surfaces in
    ``action_meta["reasoning"]`` the same way Groq does.

    Near-free pricing (as of mid-2025): ~$0.07 / 1M input tokens for V3.
    Free tier credits available on signup.
    Env var: DEEPSEEK_API_KEY
    Docs:    https://platform.deepseek.com/api-docs
    """

    def __init__(
        self,
        *,
        model: str = "deepseek-chat",
        api_key: str | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        parallel_tool_calls: bool = True,
        # R1-specific: set True when using deepseek-reasoner to capture
        # the chain-of-thought reasoning trace in action metadata.
        capture_reasoning: bool = True,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.parallel_tool_calls = parallel_tool_calls
        self.capture_reasoning = capture_reasoning
        self._last_finalize_usage: dict[str, int] | None = None
        self._client = client or self._make_client(api_key=api_key)

    # ------------------------------------------------------------------
    # Client construction
    # ------------------------------------------------------------------

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. DeepSeek uses the OpenAI-compatible API. "
                "Install with: pip install openai"
            ) from exc

        key = api_key or require_env("DEEPSEEK_API_KEY")
        return OpenAI(
            base_url="https://api.deepseek.com/v1",
            api_key=key,
        )

    # ------------------------------------------------------------------
    # Message / tool formatting
    # ------------------------------------------------------------------

    def _to_deepseek_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            converted = format_openai_like_message(
                msg,
                allow_images=False,   # DeepSeek V3/R1 are text-only
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

    def _to_deepseek_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
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

    def _is_reasoner(self) -> bool:
        """True when the selected model is a reasoning (R1) variant."""
        return "reasoner" in self.model or "r1" in self.model.lower()

    # ------------------------------------------------------------------
    # Core protocol methods
    # ------------------------------------------------------------------

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        deepseek_messages = self._to_deepseek_messages(messages)
        deepseek_tools = self._to_deepseek_tools(tools)
        is_reasoner = self._is_reasoner()

        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": deepseek_messages,
            "temperature": self.temperature,
        }

        # deepseek-reasoner does not support tool_choice or parallel_tool_calls
        if deepseek_tools and not is_reasoner:
            request_args["tools"] = deepseek_tools
            request_args["tool_choice"] = self.tool_choice
            try:
                request_args["parallel_tool_calls"] = self.parallel_tool_calls
                response = self._client.chat.completions.create(**request_args)
            except TypeError:
                request_args.pop("parallel_tool_calls", None)
                response = self._client.chat.completions.create(**request_args)
        else:
            response = self._client.chat.completions.create(**request_args)

        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)

        # R1 exposes chain-of-thought in reasoning_content
        reasoning: str | None = None
        if self.capture_reasoning:
            reasoning = getattr(message, "reasoning_content", None)

        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "deepseek", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if reasoning:
            action_meta["reasoning"] = reasoning

        if tool_calls:
            mtp_calls: list[ToolCall] = []
            id_by_index: dict[int, str] = {}
            serialized_tool_calls: list[dict[str, Any]] = []
            call_reasoning: str | None = None
            if isinstance(reasoning, str) and reasoning.strip():
                call_reasoning = reasoning.strip()

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
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                        "reasoning": call_reasoning,
                    }
                )

            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "deepseek", "model": self.model},
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
        deepseek_messages = self._to_deepseek_messages(messages)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=deepseek_messages,
            temperature=self.temperature,
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    def capabilities(self) -> ProviderCapabilities:
        is_reasoner = self._is_reasoner()
        return ProviderCapabilities(
            provider="deepseek",
            supports_tool_calling=not is_reasoner,
            supports_parallel_tool_calls=bool(self.parallel_tool_calls) and not is_reasoner,
            input_modalities=["text"],
            supports_tool_media_output=False,
            supports_finalize_streaming=False,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=bool(self.capture_reasoning),
            structured_output_support=STRUCTURED_OUTPUT_CLIENT_VALIDATED,
            supports_native_async=False,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
