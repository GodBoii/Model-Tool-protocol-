"""Audit repros. Strict xfails document unresolved defects, not passing behavior."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
from types import SimpleNamespace as NS
from typing import Any

import pytest

from mtp.agent import Agent, AgentAction
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolRiskLevel, ToolSpec
from mtp.providers.common import openai_like_tool_call_plan_payload
from mtp.runtime import ToolRegistry
from mtp.schema import PlanValidationError, validate_execution_plan


ADAPTERS = [
    ("openai", "OpenAIToolCallingProvider"), ("groq", "GroqToolCallingProvider"),
    ("openrouter", "OpenRouterToolCallingProvider"), ("anthropic", "AnthropicToolCallingProvider"),
    ("gemini", "GeminiToolCallingProvider"), ("cohere", "CohereToolCallingProvider"),
    ("mistral", "MistralToolCallingProvider"), ("cerebras", "CerebrasToolCallingProvider"),
    ("deepseek", "DeepSeekToolCallingProvider"), ("sambanova", "SambaNovaToolCallingProvider"),
    ("together", "TogetherAIToolCallingProvider"), ("fireworks", "FireworksAIToolCallingProvider"),
    ("xiaomi", "XiaomiToolCallingProvider"), ("ollama", "OllamaToolCallingProvider"),
    ("lmstudio", "LMStudioToolCallingProvider"),
]


def provider_response(provider: str, dependent: bool):
    second = {"$ref": 0} if dependent else 9
    calls = [
        NS(id="wire1", function=NS(name="audit_echo", arguments=json.dumps({"value": 7}))),
        NS(id="wire2", function=NS(name="audit_echo", arguments=json.dumps({"value": second}))),
    ]
    if provider == "anthropic":
        return NS(content=[NS(type="tool_use", id=c.id, name=c.function.name,
                              input=json.loads(c.function.arguments)) for c in calls])
    if provider == "gemini":
        return NS(text="", candidates=[NS(content=NS(parts=[
            NS(function_call=NS(name=c.function.name, args=json.loads(c.function.arguments)), text=None)
            for c in calls]))])
    if provider == "cohere":
        return NS(message=NS(tool_calls=calls, content=[]), usage=None)
    if provider == "ollama":
        return {"message": {"content": "", "tool_calls": [
            {"id": c.id, "function": {"name": c.function.name,
                                      "arguments": json.loads(c.function.arguments)}} for c in calls]}}
    return NS(choices=[NS(message=NS(tool_calls=calls, content=""))])


@pytest.mark.parametrize("provider,class_name", ADAPTERS)
@pytest.mark.parametrize("dependent", [False, True], ids=["parallel", "sequential"])
def test_all_adapters_execute_native_batches(provider, class_name, dependent):
    """Exercise adapter parsing and the real runtime, without provider network calls."""
    response = provider_response(provider, dependent)
    recorded = []

    def create(**kwargs):
        recorded.append(kwargs)
        return response

    client = NS(chat=NS(completions=NS(create=create), complete=create),
                messages=NS(create=create), models=NS(generate_content=create))
    if provider == "cohere":
        client.chat = create
    elif provider == "ollama":
        client.chat = create
    cls = getattr(importlib.import_module(f"mtp.providers.{provider}_provider"), class_name)
    adapter = cls(client=client)
    spec = ToolSpec("audit_echo", "Return value", {"type": "object", "properties": {
        "value": {"type": "integer"}}, "required": ["value"]})
    action = adapter.next_action([{"role": "user", "content": "Use the tools"}], [spec])
    assert action.plan is not None
    assert [b.mode for b in action.plan.batches] == (["sequential", "sequential"] if dependent else ["parallel"])
    registry = ToolRegistry()
    registry.register_tool(spec, lambda value: value)
    results = asyncio.run(registry.execute_plan(action.plan))
    assert [r.output for r in results] == ([7, 7] if dependent else [7, 9])
    assert all(r.success for r in results)
    assert recorded


def test_parallel_handlers_overlap_without_timing_assumptions():
    async def probe():
        registry = ToolRegistry()
        entered = []
        barrier = asyncio.Event()

        async def handler(value):
            entered.append(value)
            if len(entered) == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), timeout=2)
            return value

        registry.register_tool(ToolSpec("echo", "echo"), handler)
        plan = ExecutionPlan([ToolBatch("parallel", [
            ToolCall("a", "echo", {"value": 1}), ToolCall("b", "echo", {"value": 2})])])
        results = await registry.execute_plan(plan)
        assert [r.output for r in results] == [1, 2]
        assert all(r.success for r in results)

    asyncio.run(probe())


@pytest.mark.parametrize("calls", [
    [ToolCall("a", "echo"), ToolCall("a", "echo")],
    [ToolCall("a", "echo", depends_on=["absent"])],
    [ToolCall("a", "echo", depends_on=["b"]), ToolCall("b", "echo", depends_on=["a"])],
])
def test_invalid_plan_is_rejected_before_side_effects(calls):
    registry = ToolRegistry()
    invoked = []
    registry.register_tool(ToolSpec("echo", "echo"), lambda: invoked.append(True))
    with pytest.raises(PlanValidationError):
        asyncio.run(registry.execute_plan(ExecutionPlan([ToolBatch("sequential", calls)])))
    assert not invoked


@pytest.mark.xfail(strict=True, reason="A01: parallel batches deduplicate non-cacheable write calls")
def test_identical_write_calls_execute_twice():
    invoked = []
    registry = ToolRegistry()
    registry.register_tool(ToolSpec("write", "append", risk_level=ToolRiskLevel.WRITE),
                           lambda value: invoked.append(value))
    # Explicitly allow this synthetic in-memory write.
    from mtp.policy import PolicyDecision, RiskPolicy
    registry.policy = RiskPolicy(by_risk={ToolRiskLevel.WRITE: PolicyDecision.ALLOW})
    plan = ExecutionPlan([ToolBatch("parallel", [
        ToolCall("a", "write", {"value": 1}), ToolCall("b", "write", {"value": 1})])])
    asyncio.run(registry.execute_plan(plan))
    assert invoked == [1, 1]


@pytest.mark.xfail(strict=True, reason="A02: failed prerequisites still permit dependent handlers")
def test_failed_prerequisite_blocks_dependent_handler():
    registry = ToolRegistry()
    invoked = []

    def fail():
        raise ValueError("synthetic failure")

    registry.register_tool(ToolSpec("fail", "fail"), fail)
    registry.register_tool(ToolSpec("dependent", "dependent"), lambda: invoked.append(True))
    plan = ExecutionPlan([ToolBatch("sequential", [
        ToolCall("a", "fail"), ToolCall("b", "dependent", depends_on=["a"])])])
    asyncio.run(registry.execute_plan(plan))
    assert not invoked


class ScriptedProvider:
    def __init__(self, actions: list[AgentAction]) -> None:
        self.actions = iter(actions)

    def next_action(self, messages, tools):
        return next(self.actions, AgentAction(response_text="done"))

    def finalize(self, messages, tool_results):
        return "done"


def native_action(calls):
    payload = openai_like_tool_call_plan_payload(provider="audit", model="audit", tool_calls=calls)
    return AgentAction(plan=payload["plan"], metadata=payload["metadata"])


def wire_call(call_id, value):
    return {"id": call_id, "function": {"name": "echo", "arguments": json.dumps({"value": value})}}


@pytest.mark.xfail(strict=True, reason="A03: documented references to previous rounds are rejected")
def test_reference_can_use_a_previous_round_result():
    registry = ToolRegistry()
    registry.register_tool(ToolSpec("echo", "echo"), lambda value: value)
    provider = ScriptedProvider([
        native_action([wire_call("a", 7)]), native_action([wire_call("b", {"$ref": "a"})]),
    ])
    output = Agent(provider=provider, tools=registry).run_output("synthetic", max_rounds=3)
    assert output.tool_results[0].output == 7


@pytest.mark.xfail(strict=True, reason="A04: trimming cached calls leaves unanswered wire tool IDs")
def test_cache_trimming_keeps_tool_history_complete():
    registry = ToolRegistry()
    registry.register_tool(ToolSpec("echo", "echo", cache_ttl_seconds=60), lambda value: value)
    provider = ScriptedProvider([
        native_action([wire_call("a", 7)]),
        native_action([wire_call("b", 7), wire_call("c", 9)]),
    ])
    output = Agent(provider=provider, tools=registry).run_output("synthetic", max_rounds=3)
    declared = {c["id"] for m in output.messages for c in m.get("tool_calls", [])}
    answered = {m["tool_call_id"] for m in output.messages if m.get("role") == "tool"}
    assert declared <= answered


@pytest.mark.xfail(strict=True, reason="A05: history message limit can orphan tool results")
def test_history_limit_preserves_complete_tool_groups():
    registry = ToolRegistry()
    registry.register_tool(ToolSpec("echo", "echo"), lambda value: value)
    provider = ScriptedProvider([native_action([wire_call("a", 7), wire_call("b", 9)])])
    output = Agent(provider=provider, tools=registry, max_history_messages=3).run_output("synthetic", max_rounds=2)
    declared = {c["id"] for m in output.messages for c in m.get("tool_calls", [])}
    answered = {m["tool_call_id"] for m in output.messages if m.get("role") == "tool"}
    assert answered <= declared


@pytest.mark.xfail(strict=True, reason="A06: Anthropic receives invalid dotted built-in tool names")
def test_anthropic_tool_names_meet_official_contract():
    from mtp.providers.anthropic_provider import AnthropicToolCallingProvider
    provider = AnthropicToolCallingProvider(client=NS())
    tools = provider._to_anthropic_tools([ToolSpec("calculator.add", "add")])
    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", tools[0]["name"])


@pytest.mark.xfail(strict=True, reason="A07: DeepSeek reasoning models never receive tools")
def test_deepseek_reasoner_receives_tools():
    from mtp.providers.deepseek_provider import DeepSeekToolCallingProvider
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return NS(choices=[NS(message=NS(content="done", tool_calls=None))])

    provider = DeepSeekToolCallingProvider(model="deepseek-reasoner",
                                          client=NS(chat=NS(completions=NS(create=create))))
    provider.next_action([{"role": "user", "content": "use echo"}], [ToolSpec("echo", "echo")])
    assert captured[0].get("tools")


@pytest.mark.xfail(strict=True, reason="A08: Gemini rebuilds model parts and loses thought signatures")
def test_gemini_preserves_function_call_thought_signature():
    from mtp.providers.gemini_provider import GeminiToolCallingProvider
    client = NS(models=NS(generate_content=lambda **kwargs: NS(text="", candidates=[NS(content=NS(parts=[
        NS(function_call=NS(name="echo", args={"value": 7}), text=None, thought_signature=b"opaque-synthetic"),
    ]))])))
    provider = GeminiToolCallingProvider(client=client)
    action = provider.next_action([{"role": "user", "content": "use echo"}], [ToolSpec("echo", "echo")])
    contents, _ = provider._to_gemini_payload([action.metadata["assistant_tool_message"]])
    assert getattr(contents[0].parts[0], "thought_signature", None) == b"opaque-synthetic"


@pytest.mark.xfail(strict=True, reason="A09: Ollama streaming overwrites earlier chunks' tool calls")
def test_ollama_stream_keeps_calls_from_separate_chunks():
    from mtp.providers.ollama_provider import OllamaToolCallingProvider
    chunks = [{"message": {"tool_calls": [{"function": {"name": "echo", "arguments": {"value": v}}}]}}
              for v in [7, 9]]
    provider = OllamaToolCallingProvider(client=NS(chat=lambda **kwargs: iter(chunks)))
    actions = list(provider.stream_next_action([{"role": "user", "content": "use echo"}], [ToolSpec("echo", "echo")]))
    action = actions[-1]
    assert sum(len(b.calls) for b in action.plan.batches) == 2
