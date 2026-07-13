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
    USAGE_METRICS_BASIC,
    calls_to_dependency_batches,
    extract_refs,
    normalize_refs,
    safe_load_arguments,
)


class CohereToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for Cohere.

    Cohere has its OWN tool calling format — completely different from
    OpenAI. This adapter handles the full translation between MTP's
    protocol and Cohere's native API format.

    Best models:
        - command-a-03-2025         : Most capable, best tool use (default)
        - command-r-plus-08-2024    : Strong RAG + multi-step tool use
        - command-r-08-2024         : Fast, cheaper, solid tool calling
        - command-r7b-12-2024       : Lightweight, near-free

    Strengths over other providers:
        - Native RAG / document grounding
        - Multi-step agentic tool use built into the model
        - Structured JSON output
        - Excellent at complex reasoning chains

    Free tier : https://dashboard.cohere.com  (trial key, no credit card)
    Env var   : COHERE_API_KEY
    Docs      : https://docs.cohere.com/reference/chat
    Install   : pip install cohere
    """

    def __init__(
        self,
        *,
        model: str = "command-a-03-2025",
        api_key: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        preamble: str | None = None,
        force_single_step: bool = False,
        strict_tools: bool = True,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.preamble = preamble          # Cohere's version of system prompt
        self.force_single_step = force_single_step
        self.strict_tools = strict_tools
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._last_stream_finish_reason: str | None = None
        self._client = client or self._make_client(api_key=api_key)

    # ------------------------------------------------------------------
    # Client construction
    # ------------------------------------------------------------------

    def _make_client(self, api_key: str | None) -> Any:
        try:
            import cohere
        except ImportError as exc:
            raise ImportError(
                "`cohere` not installed. Install with: pip install cohere"
            ) from exc

        key = api_key or require_env("COHERE_API_KEY")
        return cohere.ClientV2(api_key=key)

    # ------------------------------------------------------------------
    # Tool formatting  (Cohere native format)
    # ------------------------------------------------------------------

    def _to_cohere_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        """
        Cohere tool format:
        {
            "type": "function",
            "function": {
                "name": ...,
                "description": ...,
                "parameters": { JSON Schema }
            }
        }
        Very similar to OpenAI but uses ClientV2 which accepts this shape.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name.replace(".", "__"),
                    "description": tool.description,
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
        ]

    # ------------------------------------------------------------------
    # Message formatting  (Cohere V2 API uses OpenAI-compatible messages)
    # ------------------------------------------------------------------

    def _to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, default=str)
        except Exception:
            return str(content)

    def _to_cohere_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Cohere V2 API accepts OpenAI-style message format:
        system / user / assistant / tool roles.
        Tool results use tool_call_id + content on a 'tool' role message.
        """
        formatted: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")

            if role == "system":
                content = self._to_text(msg.get("content", ""))
                if content.strip():
                    formatted.append({"role": "system", "content": content})

            elif role == "user":
                formatted.append({
                    "role": "user",
                    "content": self._to_text(msg.get("content", "")),
                })

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
                formatted.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": self._to_text(msg.get("content", "")),
                })

        return formatted

    # ------------------------------------------------------------------
    # Usage extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _read_value(value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    def _extract_cohere_usage(self, response: Any) -> dict[str, int] | None:
        """Normalize Cohere V2 usage from chat responses or message-end events.

        Cohere reports both actual token counts and billable units.  They can
        differ substantially (for example because the API injects model
        context), so retain both instead of silently replacing token counts
        with billed values.
        """
        usage = self._read_value(response, "usage")
        if usage is None:
            delta = self._read_value(response, "delta")
            usage = self._read_value(delta, "usage")
        if usage is None:
            return None

        result: dict[str, int] = {}
        tokens = self._read_value(usage, "tokens")
        billed = self._read_value(usage, "billed_units")

        if tokens is not None:
            inp = self._read_value(tokens, "input_tokens")
            out = self._read_value(tokens, "output_tokens")
            if inp is not None:
                result["input_tokens"] = int(inp)
            if out is not None:
                result["output_tokens"] = int(out)
            if inp is not None and out is not None:
                result["total_tokens"] = int(inp) + int(out)

        if billed is not None:
            billed_inp = self._read_value(billed, "input_tokens")
            billed_out = self._read_value(billed, "output_tokens")
            if billed_inp is not None:
                result["billed_input_tokens"] = int(billed_inp)
            if billed_out is not None:
                result["billed_output_tokens"] = int(billed_out)

            # Older/private Cohere deployments may omit ``tokens``. Keep the
            # provider-neutral totals useful in that case while still exposing
            # that the source was billed usage.
            if tokens is None:
                if billed_inp is not None:
                    result["input_tokens"] = int(billed_inp)
                if billed_out is not None:
                    result["output_tokens"] = int(billed_out)
                if billed_inp is not None and billed_out is not None:
                    result["total_tokens"] = int(billed_inp) + int(billed_out)
        return result or None

    # ------------------------------------------------------------------
    # Core protocol methods
    # ------------------------------------------------------------------

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        cohere_messages = self._to_cohere_messages(messages)
        cohere_tools = self._to_cohere_tools(tools)

        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": cohere_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if cohere_tools:
            request_args["tools"] = cohere_tools
            request_args["strict_tools"] = self.strict_tools
        if self.preamble:
            # Inject preamble as leading system message if not already present
            has_system = any(m.get("role") == "system" for m in cohere_messages)
            if not has_system:
                request_args["messages"] = [
                    {"role": "system", "content": self.preamble}
                ] + cohere_messages

        response = self._client.chat(**request_args)
        message = response.message
        tool_calls = getattr(message, "tool_calls", None) or []
        usage = self._extract_cohere_usage(response)
        action_meta: dict[str, Any] = {"provider": "cohere", "model": self.model}
        if usage:
            action_meta["usage"] = usage

        # Extract response text from content blocks
        response_text = ""
        content_blocks = getattr(message, "content", None) or []
        if isinstance(content_blocks, str):
            response_text = content_blocks
        elif isinstance(content_blocks, list):
            texts = []
            for block in content_blocks:
                if getattr(block, "type", None) == "text":
                    texts.append(getattr(block, "text", ""))
                elif isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
            response_text = "\n".join(texts).strip()

        if tool_calls:
            mtp_calls: list[ToolCall] = []
            id_by_index: dict[int, str] = {}
            serialized_tool_calls: list[dict[str, Any]] = []

            for idx, tc in enumerate(tool_calls):
                call_id = getattr(tc, "id", None) or f"call_{idx}"
                id_by_index[idx] = call_id

                fn = getattr(tc, "function", None)
                if fn is None:
                    continue
                # Map back sanitized names (replace __ with .)
                fn_name = getattr(fn, "name", "") or ""
                if "__" in fn_name:
                    fn_name = fn_name.replace("__", ".")
                
                raw_arguments = getattr(fn, "arguments", "{}")
                parsed_args = safe_load_arguments(raw_arguments)
                normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=idx)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))

                mtp_calls.append(
                    ToolCall(
                        id=call_id,
                        name=fn_name,
                        arguments=normalized_args,
                        depends_on=depends_on,
                    )
                )
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": fn_name,
                            "arguments": raw_arguments if isinstance(raw_arguments, str)
                                         else json.dumps(raw_arguments),
                        },
                    }
                )

            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "cohere", "model": self.model},
            )
            return AgentAction(
                plan=plan,
                metadata={
                    **action_meta,
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": response_text,
                        "tool_calls": serialized_tool_calls,
                    },
                },
            )

        return AgentAction(response_text=response_text, metadata=action_meta)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        del tool_results  # Tool results are already represented in ``messages``.
        response = self._client.chat(**self._finalize_request(messages))
        self._last_finalize_usage = self._extract_cohere_usage(response) or None
        message = response.message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        content_blocks = getattr(message, "content", None) or []
        if isinstance(content_blocks, str):
            return content_blocks or "Done."
        texts = []
        for block in content_blocks:
            if getattr(block, "type", None) == "text":
                texts.append(getattr(block, "text", ""))
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts).strip() or "Done."

    def _finalize_request(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        cohere_messages = self._to_cohere_messages(messages)
        if self.preamble and not any(message.get("role") == "system" for message in cohere_messages):
            cohere_messages = [{"role": "system", "content": self.preamble}, *cohere_messages]
        return {
            "model": self.model,
            "messages": cohere_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    def finalize_stream(
        self,
        messages: list[dict[str, Any]],
        tool_results: list[ToolResult],
    ) -> Iterator[str]:
        """Stream final text using Cohere V2's native ``chat_stream`` API.

        V2 text is emitted only by ``content-delta`` events. Usage and the
        finish reason live on the terminal ``message-end`` event. Both Cohere
        SDK model objects and decoded event dictionaries are accepted to keep
        this adapter compatible with SDK transports and test doubles.
        """
        del tool_results  # Tool results are already represented in ``messages``.
        self._last_stream_usage = None
        self._last_finalize_usage = None
        self._last_stream_finish_reason = None

        stream = self._client.chat_stream(**self._finalize_request(messages))
        for event in stream:
            event_type = self._read_value(event, "type")
            if event_type == "content-delta":
                delta = self._read_value(event, "delta")
                message = self._read_value(delta, "message")
                content = self._read_value(message, "content")
                text = self._read_value(content, "text")
                if isinstance(text, str) and text:
                    yield text
                continue

            if event_type == "message-end":
                usage = self._extract_cohere_usage(event)
                self._last_stream_usage = usage
                self._last_finalize_usage = usage
                delta = self._read_value(event, "delta")
                finish_reason = self._read_value(delta, "finish_reason")
                self._last_stream_finish_reason = (
                    finish_reason if isinstance(finish_reason, str) else None
                )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="cohere",
            supports_tool_calling=True,
            supports_parallel_tool_calls=False,
            input_modalities=["text"],
            supports_tool_media_output=False,
            supports_finalize_streaming=True,
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
