from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Ensure tests exercise this checkout even when another MTP worktree is installed
# in editable mode in the active interpreter.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mtp.agent import AgentAction
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from mtp.providers.common import ProviderCapabilities, USAGE_METRICS_NONE


class _ConstantActionProvider:
    """Provider stub that returns the same action for every planning round."""

    def __init__(self, action: AgentAction) -> None:
        self._action = action
        self._calls: list[list[dict[str, Any]]] = []

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        self._calls.append(messages)
        return self._action

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        parts = [
            f"{result.tool_name}={result.output}"
            if result.success
            else f"{result.tool_name}=ERROR:{result.error}"
            for result in tool_results
        ]
        return "; ".join(parts) if parts else "No tools executed."

    def finalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> Iterator[str]:
        text = self.finalize(messages, tool_results)
        for index in range(0, len(text), 10):
            yield text[index : index + 10]

    async def anext_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        return self.next_action(messages, tools)

    async def afinalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        return self.finalize(messages, tool_results)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="constant_stub",
            supports_tool_calling=True,
            supports_parallel_tool_calls=True,
            input_modalities=["text"],
            supports_finalize_streaming=True,
            usage_metrics_quality=USAGE_METRICS_NONE,
        )


class _TextOnlyProvider:
    """Provider stub that returns a direct text response."""

    def __init__(self, text: str = "Hello from stub.") -> None:
        self.text = text

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return AgentAction(response_text=self.text)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return self.text

    def finalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> Iterator[str]:
        yield self.text

    async def anext_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        return self.next_action(messages, tools)

    async def afinalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        return self.finalize(messages, tool_results)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="text_only_stub",
            supports_tool_calling=False,
            supports_finalize_streaming=False,
            usage_metrics_quality=USAGE_METRICS_NONE,
        )


class _MultiRoundProvider:
    """Provider stub that returns a sequence of plans followed by text."""

    def __init__(self, plans: list[AgentAction], final_text: str = "Done.") -> None:
        self._plans = list(plans)
        self._final_text = final_text
        self._round = 0

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        if self._round < len(self._plans):
            action = self._plans[self._round]
            self._round += 1
            return action
        return AgentAction(response_text=self._final_text)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return self._final_text

    def finalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> Iterator[str]:
        yield self._final_text

    async def anext_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        return self.next_action(messages, tools)

    async def afinalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        return self.finalize(messages, tool_results)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="multi_round_stub",
            supports_tool_calling=True,
            supports_parallel_tool_calls=True,
            usage_metrics_quality=USAGE_METRICS_NONE,
        )


def make_tool_spec(name: str = "test.echo", **overrides: Any) -> ToolSpec:
    values = {
        "name": name,
        "description": "Test tool.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "risk_level": ToolRiskLevel.READ_ONLY,
    }
    values.update(overrides)
    return ToolSpec(**values)


def make_echo_handler():
    def echo(text: str) -> str:
        return text

    return echo


def make_plan(*calls: ToolCall, mode: str = "sequential") -> ExecutionPlan:
    return ExecutionPlan(batches=[ToolBatch(mode=mode, calls=list(calls))])


def make_call(
    call_id: str = "c1",
    name: str = "test.echo",
    args: dict[str, Any] | None = None,
    depends_on: list[str] | None = None,
) -> ToolCall:
    return ToolCall(
        id=call_id,
        name=name,
        arguments=args or {"text": "hi"},
        depends_on=depends_on or [],
    )
