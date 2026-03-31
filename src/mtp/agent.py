from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .protocol import ExecutionPlan, ToolResult, ToolSpec
from .runtime import ToolRegistry


@dataclass(slots=True)
class AgentAction:
    response_text: str | None = None
    plan: ExecutionPlan | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderAdapter(Protocol):
    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        ...

    def finalize(
        self,
        messages: list[dict[str, Any]],
        tool_results: list[ToolResult],
    ) -> str:
        ...


class Agent:
    def __init__(
        self,
        provider: ProviderAdapter,
        registry: ToolRegistry,
        *,
        debug_mode: bool = False,
        debug_logger: Callable[[str], None] | None = None,
        debug_max_chars: int = 600,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.debug_mode = debug_mode
        self.debug_logger = debug_logger or print
        self.debug_max_chars = debug_max_chars
        self.messages: list[dict[str, Any]] = []

    def _debug(self, text: str) -> None:
        if self.debug_mode:
            self.debug_logger(f"[MTP DEBUG] {text}")

    def _short(self, value: Any) -> str:
        try:
            if isinstance(value, str):
                text = value
            else:
                text = json.dumps(value, default=str)
        except Exception:
            text = repr(value)
        if len(text) <= self.debug_max_chars:
            return text
        return text[: self.debug_max_chars] + "...<truncated>"

    def run(self, user_text: str) -> str:
        return self.run_loop(user_text=user_text, max_rounds=1)

    def run_loop(self, user_text: str, max_rounds: int = 5) -> str:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")

        self.messages.append({"role": "user", "content": user_text})
        self._debug(f"user_message={user_text!r}")
        tools = self.registry.list_tools()
        self._debug(f"tools_available={len(tools)}")
        self._debug(f"tool_names={[tool.name for tool in tools]}")
        last_results: list[ToolResult] = []

        for round_idx in range(1, max_rounds + 1):
            self._debug(f"round={round_idx} start")
            self._debug(
                "llm_request_messages="
                + self._short(
                    [
                        {
                            "role": msg.get("role"),
                            "content": msg.get("content"),
                            "tool_calls": msg.get("tool_calls"),
                            "tool_call_id": msg.get("tool_call_id"),
                            "tool_name": msg.get("tool_name"),
                        }
                        for msg in self.messages
                    ]
                )
            )
            action = self.provider.next_action(self.messages, tools)

            if action.response_text and action.plan is None:
                self._debug("provider returned direct response (no tool plan)")
                self._debug(f"assistant_response={self._short(action.response_text)}")
                self.messages.append({"role": "assistant", "content": action.response_text})
                return action.response_text

            if action.plan is None:
                self._debug("provider returned no plan; breaking loop")
                break

            call_count = sum(len(batch.calls) for batch in action.plan.batches)
            self._debug(f"plan_received batches={len(action.plan.batches)} calls={call_count}")
            for batch_idx, batch in enumerate(action.plan.batches, start=1):
                call_names = [call.name for call in batch.calls]
                self._debug(f"batch#{batch_idx} mode={batch.mode} calls={call_names}")

            assistant_tool_message = action.metadata.get("assistant_tool_message")
            if isinstance(assistant_tool_message, dict):
                self._debug("assistant tool-call message appended")
                self._debug(f"assistant_tool_message={self._short(assistant_tool_message)}")
                self.messages.append(assistant_tool_message)

            last_results = asyncio.run(self.registry.execute_plan(action.plan))
            self._debug(f"executed_calls={len(last_results)}")
            for result in last_results:
                self._debug(
                    "tool_result "
                    f"id={result.call_id} tool={result.tool_name} "
                    f"success={result.success} cached={result.cached} "
                    f"approval={result.approval}"
                )
                self._debug(
                    f"tool_result_payload id={result.call_id} data={self._short(result.output if result.success else result.error)}"
                )
            tool_messages = [
                {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "tool_name": result.tool_name,
                    "content": result.output if result.success else result.error,
                    "success": result.success,
                    "cached": result.cached,
                }
                for result in last_results
            ]
            self.messages.extend(tool_messages)

        self._debug("calling provider.finalize")
        final_text = self.provider.finalize(self.messages, last_results)
        self.messages.append({"role": "assistant", "content": final_text})
        self._debug(f"final response generated text={self._short(final_text)}")
        return final_text
