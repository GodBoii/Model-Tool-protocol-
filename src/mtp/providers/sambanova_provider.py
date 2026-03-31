from __future__ import annotations

import json
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import calls_to_dependency_batches, extract_refs, normalize_refs, safe_load_arguments


class SambaNovaToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for SambaNova Cloud.
    Ultra-fast inference for Llama models.
    """

    def __init__(
        self,
        *,
        model: str = "Meta-Llama-3.1-70B-Instruct",
        api_key: str | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.tool_choice = tool_choice
        self._client = client or self._make_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. Please install using `pip install openai`"
            ) from exc

        key = api_key or require_env("SAMBANOVA_API_KEY")
        return OpenAI(
            base_url="https://api.sambanova.ai/v1",
            api_key=key,
        )

    def _to_openai_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "tool":
                content = msg.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content)
                formatted.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id"),
                    "content": content
                })
            else:
                new_msg = {"role": role, "content": msg.get("content") or ""}
                if "tool_calls" in msg:
                    new_msg["tool_calls"] = msg["tool_calls"]
                formatted.append(new_msg)
        return formatted

    def _to_openai_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
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
        openai_messages = self._to_openai_messages(messages)
        openai_tools = self._to_openai_tools(tools)

        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": self.temperature,
        }
        if openai_tools:
            request_args["tools"] = openai_tools
            request_args["tool_choice"] = self.tool_choice

        response = self._client.chat.completions.create(**request_args)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)

        if tool_calls:
            mtp_calls: list[ToolCall] = []
            id_by_index: dict[int, str] = {}
            serialized_tool_calls: list[dict[str, Any]] = []
            for idx, tc in enumerate(tool_calls):
                call_id = tc.id or f"call_{idx}"
                id_by_index[idx] = call_id
                parsed_args = safe_load_arguments(tc.function.arguments)
                normalized_args = normalize_refs(parsed_args, id_by_index)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                mtp_calls.append(
                    ToolCall(
                        id=call_id,
                        name=tc.function.name,
                        arguments=normalized_args,
                        depends_on=depends_on,
                    )
                )
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                    }
                )

            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "sambanova", "model": self.model}
            )
            
            return AgentAction(
                plan=plan,
                metadata={
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": serialized_tool_calls,
                    }
                },
            )

        return AgentAction(response_text=message.content or "")

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        openai_messages = self._to_openai_messages(messages)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            temperature=self.temperature,
        )
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."
