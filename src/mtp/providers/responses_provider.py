"""Stateless Responses API with native output-item replay through saved sessions."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

from ..agent import AgentAction
from ..config import require_env
from ..protocol import ToolSpec
from .common import extract_usage_metrics
from .compatible_provider import OpenAICompatibleToolCallingProvider, read_value


def output_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return copy.deepcopy(item)
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    raise TypeError("Responses output items must be SDK models or dictionaries.")


class OpenAIResponsesToolCallingProvider(OpenAICompatibleToolCallingProvider):
    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        max_output_tokens: int | None = 1024,
        reasoning_effort: str | None = None,
        **kwargs: Any,
    ) -> None:
        key = api_key or (
            require_env("OPENAI_API_KEY") if kwargs.get("client") is None else None
        )
        self.reasoning_effort = reasoning_effort
        super().__init__(
            model=model,
            api_key=key,
            base_url=base_url,
            provider_name="openai_responses",
            max_tokens=max_output_tokens,
            **kwargs,
        )
        if {"store", "include", "previous_response_id", "max_output_tokens", "reasoning"}.intersection(self.extra_body):
            raise ValueError("Responses extra_body cannot override state, reasoning or token settings.")

    def _input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = []
        for message in messages:
            native = message.get("responses_items")
            if message.get("role") == "assistant" and isinstance(native, list):
                items.extend(copy.deepcopy(native))
                continue
            role = message.get("role")
            if role == "tool":
                value = message.get("content", "")
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message["tool_call_id"],
                        "output": value
                        if isinstance(value, str)
                        else json.dumps(value, default=str),
                    }
                )
                continue
            if role not in {"system", "user", "assistant", "developer"}:
                continue
            content = message.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, default=str)
            if content or not message.get("tool_calls"):
                items.append({"role": role, "content": content})
            for call in message.get("tool_calls") or []:
                function = call["function"]
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": self._tool_names.wire_name(function["name"]),
                        "arguments": function["arguments"],
                    }
                )
        return items

    def _request(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "input": self._input(messages),
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if self.max_tokens is not None:
            request["max_output_tokens"] = self.max_tokens
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.reasoning_effort is not None:
            request["reasoning"] = {"effort": self.reasoning_effort}
        if self.extra_body:
            request["extra_body"] = copy.deepcopy(self.extra_body)
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "name": self._tool_names.wire_name(tool.name),
                    "description": tool.description,
                    "parameters": tool.input_schema
                    or {"type": "object", "properties": {}},
                    "strict": False,
                }
                for tool in tools
            ]
            choice = copy.deepcopy(self.tool_choice)
            if isinstance(choice, dict) and choice.get("type") == "function":
                function = choice.get("function", choice)
                choice = {
                    "type": "function",
                    "name": self._tool_names.wire_name(function["name"]),
                }
            request["tool_choice"] = choice
            if self.parallel_tool_calls is not None:
                request["parallel_tool_calls"] = self.parallel_tool_calls
        if stream:
            request["stream"] = True
        return request

    def _response_action(self, response: Any) -> AgentAction:
        status = read_value(response, "status")
        if status != "completed":
            raise ValueError(f"Responses request did not complete, status={status!r}.")
        raw_items = read_value(response, "output")
        if not isinstance(raw_items, list):
            raise TypeError("Responses request is missing output items.")
        native = [output_item(item) for item in raw_items]
        calls = []
        text = []
        for item in native:
            kind = item.get("type")
            if kind == "function_call":
                if item.get("status") not in {None, "completed"}:
                    raise ValueError("Responses returned an unfinished function call.")
                calls.append(
                    {
                        "id": item.get("call_id"),
                        "function": {
                            "name": item.get("name"),
                            "arguments": item.get("arguments"),
                        },
                    }
                )
            elif kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        text.append(part.get("text", ""))
                    elif part.get("type") == "refusal":
                        text.append(part.get("refusal", ""))
            elif kind != "reasoning":
                raise ValueError(f"Unsupported Responses output item: {kind!r}")
        action = self._action(
            "".join(text), calls, None, extract_usage_metrics(response)
        )
        assistant = action.metadata.get(
            "assistant_tool_message", action.metadata["assistant_message"]
        )
        assistant["responses_items"] = native
        action.metadata["assistant_message"] = assistant
        return action

    def next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        return self._response_action(
            self._client.responses.create(**self._request(messages, tools))
        )

    def stream_next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> Iterator[AgentAction | dict[str, Any]]:
        stream = self._client.responses.create(
            **self._request(messages, tools, stream=True)
        )
        completed = None
        try:
            for event in stream:
                kind = read_value(event, "type")
                if kind == "response.output_text.delta":
                    yield {
                        "type": "text_chunk",
                        "chunk": read_value(event, "delta", ""),
                    }
                elif kind == "response.reasoning_summary_text.delta":
                    yield {
                        "type": "reasoning_chunk",
                        "chunk": read_value(event, "delta", ""),
                    }
                elif kind == "response.completed":
                    completed = read_value(event, "response")
                elif kind in {"error", "response.failed", "response.incomplete"}:
                    raise ValueError(f"Responses stream failed with event {kind}.")
            if completed is None:
                raise ValueError(
                    "Responses stream ended without its completed response."
                )
            # Only the completed response can authorize executing fully parsed calls.
            yield self._response_action(completed)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    def capabilities(self):
        result = super().capabilities()
        result.input_modalities = ["text"]
        return result
