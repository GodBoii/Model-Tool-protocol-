from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mtp.agent import AgentAction, ProviderAdapter, RunOutput
from mtp.media import Audio, File, Image, Video
from mtp.policy import PolicyDecision, RiskPolicy
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from mtp.providers.common import (
    ProviderCapabilities,
    STRUCTURED_OUTPUT_NONE,
    USAGE_METRICS_NONE,
    USAGE_METRICS_BASIC,
)
from mtp.runtime import RegisteredTool, ToolRegistry
from mtp.tools import FunctionToolkit, mtp_tool, toolkit_from_functions


# ---------------------------------------------------------------------------
# Fake / Stub Providers
# ---------------------------------------------------------------------------

class _ConstantActionProvider:
    """Returns a fixed AgentAction every time next_action is called."""

    def __init__(self, action: AgentAction) -> None:
        self._action = action
        self._calls: list[list[dict[str, Any]]] = []

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        self._calls.append(messages)
        return self._action

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        parts = []
        for r in tool_results:
            if r.success:
                parts.append(f"{r.tool_name}={r.output}")
            else:
                parts.append(f"{r.tool_name}=ERROR:{r.error}")
        return "; ".join(parts) if parts else "No tools executed."

    def finalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> Iterator[str]:
        text = self.finalize(messages, tool_results)
        for i in range(0, len(text), 10):
            yield text[i : i + 10]

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return self.next_action(messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
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
    """Returns a direct text response, no tool calls."""

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

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return self.next_action(messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return self.finalize(messages, tool_results)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="text_only_stub",
            supports_tool_calling=False,
            supports_finalize_streaming=False,
            usage_metrics_quality=USAGE_METRICS_NONE,
        )


class _MultiRoundProvider:
    """Returns tool plans for N rounds, then text."""

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

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        return self.next_action(messages, tools)

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return self.finalize(messages, tool_results)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="multi_round_stub",
            supports_tool_calling=True,
            supports_parallel_tool_calls=True,
            usage_metrics_quality=USAGE_METRICS_NONE,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tool_spec(name: str = "test.echo", **overrides: Any) -> ToolSpec:
    defaults = dict(
        name=name,
        description="Test tool.",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        risk_level=ToolRiskLevel.READ_ONLY,
    )
    defaults.update(overrides)
    return ToolSpec(**defaults)


def make_echo_handler():
    def echo(text: str) -> str:
        return text
    return echo


def make_add_handler():
    def add(a: float, b: float) -> float:
        return a + b
    return add


def make_raising_handler(exc_type=ValueError, msg="boom"):
    def raiser(**kwargs: Any) -> None:
        raise exc_type(msg)
    return raiser


def make_plan(*calls: ToolCall, mode: str = "sequential") -> ExecutionPlan:
    return ExecutionPlan(batches=[ToolBatch(mode=mode, calls=list(calls))])


def make_call(call_id: str = "c1", name: str = "test.echo", args: dict[str, Any] | None = None, depends_on: list[str] | None = None) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args or {"text": "hi"}, depends_on=depends_on or [])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def echo_spec():
    return make_tool_spec()


@pytest.fixture
def echo_handler():
    return make_echo_handler()


@pytest.fixture
def registry_with_echo(echo_spec, echo_handler):
    reg = ToolRegistry()
    reg.register_tool(echo_spec, echo_handler)
    return reg


@pytest.fixture
def calculator_registry():
    from mtp.toolkits.calculator import CalculatorToolkit
    reg = ToolRegistry()
    reg.register_toolkit_loader("calculator", CalculatorToolkit())
    return reg


@pytest.fixture
def tmp_base(tmp_path):
    return tmp_path
