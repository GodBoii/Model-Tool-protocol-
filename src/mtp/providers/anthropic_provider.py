from __future__ import annotations

import json
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult, ToolSpec


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
        except ImportError:
            raise ImportError(
                "anthropic is not installed. Anthropic provider requires: pip install anthropic"
            )

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

    def _to_anthropic_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted = []
        for msg in messages:
            role = msg.get("role")
            if role == "user":
                formatted.append({"role": "user", "content": msg["content"]})
            elif role == "assistant":
                formatted.append({"role": "assistant", "content": msg.get("content") or ""})
            elif role == "tool":
                formatted.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg["tool_call_id"],
                            "content": str(msg["content"]),
                        }
                    ],
                })
        return formatted

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        anthropic_messages = self._to_anthropic_messages(messages)
        anthropic_tools = self._to_anthropic_tools(tools)

        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=anthropic_messages,
            tools=anthropic_tools if tools else [],
            temperature=self.temperature,
        )

        calls = []
        response_text = ""
        for content in response.content:
            if content.type == "text":
                response_text = content.text
            elif content.type == "tool_use":
                calls.append(
                    ToolCall(
                        id=content.id,
                        name=content.name,
                        arguments=dict(content.input),
                    )
                )

        if calls:
            plan = ExecutionPlan(
                batches=[ToolBatch(mode="sequential", calls=calls)],
                metadata={"provider": "anthropic", "model": self.model}
            )
            return AgentAction(plan=plan)

        return AgentAction(response_text=response_text)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        anthropic_messages = self._to_anthropic_messages(messages)
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=anthropic_messages,
            temperature=self.temperature,
        )
        return response.content[0].text
