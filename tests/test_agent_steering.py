"""Steering an active run, and recovering history after a run dies mid-tool-call."""
from __future__ import annotations

from typing import Any

from mtp.agent import Agent, AgentAction
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from mtp.runtime import ToolRegistry

RUN_ID = "run-steer-test"


def _registry(handler: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec(
            name="test.echo",
            description="Echo text.",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            risk_level=ToolRiskLevel.READ_ONLY,
        ),
        handler,
    )
    return registry


class _ScriptedProvider:
    """Round 1 calls test.echo; later rounds answer with text."""

    def __init__(self, on_call: Any = None) -> None:
        self.seen: list[list[dict[str, Any]]] = []
        self._on_call = on_call

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        self.seen.append([dict(m) for m in messages])
        if self._on_call is not None:
            self._on_call(len(self.seen))
        if len(self.seen) == 1:
            call = ToolCall(id="c1", name="test.echo", arguments={"text": "hi"})
            return AgentAction(plan=ExecutionPlan(batches=[ToolBatch(mode="sequential", calls=[call])]))
        return AgentAction(response_text="done")

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        return "finalized"


def _user_texts(messages: list[dict[str, Any]]) -> list[str]:
    return [str(m.get("content")) for m in messages if m.get("role") == "user"]


def test_steering_during_tools_reaches_the_next_round() -> None:
    holder: dict[str, Agent] = {}

    def echo(text: str) -> str:
        assert holder["agent"].steer_run(RUN_ID, "also check the tests") is True
        return text

    provider = _ScriptedProvider()
    agent = Agent(provider=provider, tools=_registry(echo))
    holder["agent"] = agent
    events = list(agent.run_loop_events("fix the bug", max_rounds=4, run_id=RUN_ID))

    types = [e["type"] for e in events]
    assert "steer_applied" in types
    steer_event = next(e for e in events if e["type"] == "steer_applied")
    assert steer_event["messages"] == ["also check the tests"]
    second_round_users = _user_texts(provider.seen[1])
    assert second_round_users[-1].endswith("also check the tests")
    assert types[-1] == "run_completed"
    assert agent.take_unapplied_steering(RUN_ID) == []


def test_steering_after_the_last_round_is_returned_unapplied() -> None:
    holder: dict[str, Agent] = {}

    class _AnswerProvider(_ScriptedProvider):
        def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
            # Arrives while the model writes its final answer.
            assert holder["agent"].steer_run(RUN_ID, "late note") is True
            return AgentAction(response_text="answer")

    agent = Agent(provider=_AnswerProvider(), tools=_registry(lambda text: text))
    holder["agent"] = agent
    events = list(agent.run_loop_events("q", max_rounds=2, run_id=RUN_ID))
    assert "steer_applied" not in [e["type"] for e in events]
    assert agent.take_unapplied_steering(RUN_ID) == ["late note"]
    assert agent.steer_run(RUN_ID, "run is over") is False


def test_blank_steering_is_rejected() -> None:
    agent = Agent(provider=_ScriptedProvider(), tools=_registry(lambda text: text))
    agent._register_run(RUN_ID)
    assert agent.steer_run(RUN_ID, "   ") is False


def test_dangling_tool_calls_are_answered_before_the_next_run() -> None:
    provider = _ScriptedProvider()
    agent = Agent(provider=provider, tools=_registry(lambda text: text))
    # History as a crash between the tool-call message and its results leaves it.
    agent.messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a1", "type": "function", "function": {"name": "test.echo", "arguments": "{}"}},
            {"id": "a2", "type": "function", "function": {"name": "test.echo", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a1", "tool_name": "test.echo", "content": "ok"},
    ]
    agent._system_seeded = True
    list(agent.run_loop_events("second", max_rounds=3, run_id=RUN_ID))

    first_request = provider.seen[0]
    tool_ids = [m.get("tool_call_id") for m in first_request if m.get("role") == "tool"]
    assert tool_ids[:2] == ["a1", "a2"]
    placeholder = next(m for m in first_request if m.get("tool_call_id") == "a2")
    assert placeholder["success"] is False and placeholder["tool_name"] == "test.echo"
    # The placeholder sits right after the answered call, before the new prompt.
    roles = [m.get("role") for m in first_request]
    assert roles.index("tool") < roles.index("user", 1)


def test_complete_history_is_left_alone() -> None:
    agent = Agent(provider=_ScriptedProvider(), tools=_registry(lambda text: text))
    agent.messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "function": {"name": "t"}}]},
        {"role": "tool", "tool_call_id": "x", "content": "ok"},
    ]
    before = list(agent.messages)
    assert agent._repair_dangling_tool_calls() == 0
    assert agent.messages == before
