from __future__ import annotations

import json
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult, ToolSpec


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
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.parallel_tool_calls = parallel_tool_calls
        self.encourage_batch_tool_calls = encourage_batch_tool_calls
        self._last_response: Any | None = None
        self._client = client or self._make_client(api_key=api_key)

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

        if tool_calls:
            mtp_calls: list[ToolCall] = []
            serialized_tool_calls: list[dict[str, Any]] = []
            for idx, tc in enumerate(tool_calls):
                fn_name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    parsed_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    parsed_args = {"_raw_arguments": raw_args}
                call_id = tc.id or f"call_{idx}"
                mtp_calls.append(ToolCall(id=call_id, name=fn_name, arguments=parsed_args))
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": fn_name, "arguments": raw_args},
                    }
                )

            plan = ExecutionPlan(
                batches=[ToolBatch(mode="parallel", calls=mtp_calls)],
                metadata={"provider": "groq", "model": self.model},
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
        groq_messages = self._to_groq_messages(messages)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=groq_messages,
            temperature=self.temperature,
        )
        choice = response.choices[0]
        message = choice.message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; multi-round chaining is next on roadmap."
        return message.content or "Done."
