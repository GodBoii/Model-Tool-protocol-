from __future__ import annotations

import asyncio
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    STRUCTURED_OUTPUT_CLIENT_VALIDATED,
    STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA,
    USAGE_METRICS_RICH,
    extract_usage_metrics,
    format_openai_like_message,
    openai_like_tool_call_plan_payload,
)


class FireworksAIToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for Fireworks AI.

    Fireworks AI specialises in FAST open-model inference — often 3-5x faster
    than Together AI for the same model, using their FireAttention kernel.
    Excellent for complex multi-step agentic tasks where latency matters.

    Recommended models for tool calling:
        - accounts/fireworks/models/llama-v3p3-70b-instruct   (default, best)
        - accounts/fireworks/models/llama-v3p1-405b-instruct  (most capable)
        - accounts/fireworks/models/qwen2p5-72b-instruct      (top reasoning)
        - accounts/fireworks/models/deepseek-v3               (best reasoning)
        - accounts/fireworks/models/mixtral-8x22b-instruct    (fast + smart)
        - accounts/fireworks/models/firefunction-v2           (fine-tuned for tools)

    Strengths:
        - firefunction-v2: purpose-built for function/tool calling
        - FireAttention: dramatically lower latency than competitors
        - Supports structured JSON output mode
        - Image understanding on vision models
        - On-demand + serverless deployments

    Free tier : Free credits on signup — https://fireworks.ai
    Env var   : FIREWORKS_API_KEY
    Docs      : https://docs.fireworks.ai
    Install   : pip install fireworks-ai   OR   pip install openai
    """

    def __init__(
        self,
        *,
        model: str = "accounts/fireworks/models/llama-v3p3-70b-instruct",
        api_key: str | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        parallel_tool_calls: bool = True,
        max_tokens: int = 4096,
        # Fireworks supports structured JSON output — set a JSON schema here
        # to force structured responses (used in finalize for complex tasks).
        response_format: dict[str, Any] | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.parallel_tool_calls = parallel_tool_calls
        self.max_tokens = max_tokens
        self.response_format = response_format
        self._last_finalize_usage: dict[str, int] | None = None
        self._client = client or self._make_client(api_key=api_key)

    # ------------------------------------------------------------------
    # Client construction
    # ------------------------------------------------------------------

    def _make_client(self, api_key: str | None) -> Any:
        key = api_key or require_env("FIREWORKS_API_KEY")

        # Prefer the native Fireworks SDK for full feature support
        try:
            import fireworks.client as fw
            fw.api_key = key

            class _FireworksClient:
                """Thin wrapper to give a unified .chat.completions.create interface."""
                class chat:
                    class completions:
                        @staticmethod
                        def create(**kwargs: Any) -> Any:
                            from fireworks.client import ChatCompletion
                            return ChatCompletion.create(**kwargs)

            return _FireworksClient()
        except ImportError:
            pass

        # Fall back to openai client pointed at Fireworks endpoint
        try:
            from openai import OpenAI
            return OpenAI(
                base_url="https://api.fireworks.ai/inference/v1",
                api_key=key,
            )
        except ImportError as exc:
            raise ImportError(
                "Neither `fireworks-ai` nor `openai` is installed. "
                "Install with: pip install fireworks-ai  OR  pip install openai"
            ) from exc

    # ------------------------------------------------------------------
    # Message / tool formatting
    # ------------------------------------------------------------------

    def _to_fireworks_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            converted = format_openai_like_message(
                msg,
                allow_images=True,    # Vision models supported on Fireworks
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

    def _to_fireworks_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
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

    # ------------------------------------------------------------------
    # Core protocol methods
    # ------------------------------------------------------------------

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        fw_messages = self._to_fireworks_messages(messages)
        fw_tools = self._to_fireworks_tools(tools)

        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": fw_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if fw_tools:
            request_args["tools"] = fw_tools
            request_args["tool_choice"] = self.tool_choice
            request_args["parallel_tool_calls"] = self.parallel_tool_calls
        if self.response_format:
            request_args["response_format"] = self.response_format

        try:
            response = self._client.chat.completions.create(**request_args)
        except TypeError:
            request_args.pop("parallel_tool_calls", None)
            response = self._client.chat.completions.create(**request_args)

        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "fireworks", "model": self.model}
        if usage:
            action_meta["usage"] = usage

        if tool_calls:
            payload = openai_like_tool_call_plan_payload(
                provider="fireworks",
                model=self.model,
                tool_calls=list(tool_calls),
                content=message.content or "",
                use_current_index_refs=True,
            )
            return AgentAction(
                plan=payload["plan"],
                metadata={
                    **action_meta,
                    **payload["metadata"],
                },
            )

        return AgentAction(response_text=message.content or "", metadata=action_meta)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        fw_messages = self._to_fireworks_messages(messages)
        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": fw_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.response_format:
            request_args["response_format"] = self.response_format

        response = self._client.chat.completions.create(**request_args)
        self._last_finalize_usage = extract_usage_metrics(response) or None
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    def capabilities(self) -> ProviderCapabilities:
        structured = STRUCTURED_OUTPUT_NATIVE_JSON_SCHEMA if self.response_format else STRUCTURED_OUTPUT_CLIENT_VALIDATED
        return ProviderCapabilities(
            provider="fireworks",
            supports_tool_calling=True,
            supports_parallel_tool_calls=bool(self.parallel_tool_calls),
            input_modalities=["text", "image"],
            supports_tool_media_output=True,
            supports_finalize_streaming=False,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=False,
            structured_output_support=structured,
            supports_native_async=False,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
