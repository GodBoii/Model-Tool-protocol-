from __future__ import annotations

import asyncio
import json
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import calls_to_dependency_batches, extract_refs, normalize_refs


class AnthropicToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for Anthropic Claude.
    Sends tool definitions via the Anthropic Tool-Use API.
    """

    def __init__(
        self,
        *,
        model: str = "claude-3-5-sonnet-20241022",
        api_key: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = client or self._make_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "`anthropic` not installed. Please install using `pip install anthropic`"
            ) from exc

        key = api_key or require_env("ANTHROPIC_API_KEY")
        return anthropic.Anthropic(api_key=key)

    def _to_anthropic_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema or {"type": "object", "properties": {}},
            }
            for tool in tools
        ]

    def _to_anthropic_payload(self, messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        system_blocks: list[str] = []
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    system_blocks.append(content)
            elif role == "user":
                formatted.append({"role": "user", "content": msg.get("content") or ""})
            elif role == "assistant":
                assistant_message: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
                if "tool_calls" in msg:
                    assistant_message["tool_calls"] = msg["tool_calls"]
                formatted.append(assistant_message)
            elif role == "tool":
                content = msg.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content)
                formatted.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg["tool_call_id"],
                            "content": content,
                        }
                    ],
                })
        system_prompt = "\n\n".join(system_blocks) if system_blocks else None
        return system_prompt, formatted

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        system_prompt, anthropic_messages = self._to_anthropic_payload(messages)
        anthropic_tools = self._to_anthropic_tools(tools)

        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
            "tools": anthropic_tools if tools else [],
            "temperature": self.temperature,
        }
        if system_prompt:
            request["system"] = system_prompt

        response = self._client.messages.create(
            **request,
        )

        calls: list[ToolCall] = []
        serialized_tool_calls: list[dict[str, Any]] = []
        id_by_index: dict[int, str] = {}
        response_text = ""
        for idx, content in enumerate(response.content):
            if content.type == "text":
                response_text = content.text
            elif content.type == "tool_use":
                call_id = content.id or f"call_{idx}"
                id_by_index[idx] = call_id
                raw_input = dict(content.input)
                normalized_args = normalize_refs(raw_input, id_by_index)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                calls.append(
                    ToolCall(
                        id=call_id,
                        name=content.name,
                        arguments=normalized_args,
                        depends_on=depends_on,
                    )
                )
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": content.name, "arguments": json.dumps(raw_input)},
                    }
                )

        if calls:
            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(calls),
                metadata={"provider": "anthropic", "model": self.model}
            )
            return AgentAction(
                plan=plan,
                metadata={
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": response_text,
                        "tool_calls": serialized_tool_calls,
                    }
                },
            )

        return AgentAction(response_text=response_text)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        system_prompt, anthropic_messages = self._to_anthropic_payload(messages)
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
            "temperature": self.temperature,
        }
        if system_prompt:
            request["system"] = system_prompt
        response = self._client.messages.create(**request)
        texts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
        if texts:
            return "\n".join(texts).strip()
        return "Done."

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)
