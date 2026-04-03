from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, ExecutionPlan, ToolBatch, ToolCall, ToolRegistry, ToolSpec
from mtp.agent import AgentAction, ProviderAdapter
from mtp.exceptions import RetryAgentRun, StopAgentRun
from mtp.protocol import ToolResult
from mtp.runtime import RegisteredTool
from mtp.toolkits import CalculatorToolkit


class _OneCallThenTextProvider(ProviderAdapter):
    def __init__(self, tool_name: str, text: str = "done") -> None:
        self.tool_name = tool_name
        self.text = text
        self.calls = 0

    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        self.calls += 1
        if self.calls == 1:
            return AgentAction(
                plan=ExecutionPlan(
                    batches=[
                        ToolBatch(
                            mode="sequential",
                            calls=[ToolCall(id="c1", name=self.tool_name, arguments={})],
                        )
                    ]
                )
            )
        return AgentAction(response_text=self.text)

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return self.text


class _EchoFinalizeProvider(ProviderAdapter):
    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        return AgentAction(response_text='{"value": 1}')

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return messages[-1]["content"] if messages else ""


class _ConstantFinalizeProvider(ProviderAdapter):
    def __init__(self, text: str) -> None:
        self.text = text

    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        return AgentAction(response_text=self.text)

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return self.text


class _ExplodingProvider(ProviderAdapter):
    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        raise RuntimeError("boom")

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return "never"


class _DelegatingProvider(ProviderAdapter):
    def __init__(self, delegate_tool_name: str) -> None:
        self.delegate_tool_name = delegate_tool_name
        self.calls = 0

    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        self.calls += 1
        if self.calls == 1:
            return AgentAction(
                plan=ExecutionPlan(
                    batches=[
                        ToolBatch(
                            mode="sequential",
                            calls=[
                                ToolCall(
                                    id="delegate-1",
                                    name=self.delegate_tool_name,
                                    arguments={"task": "Solve: 40 + 2"},
                                )
                            ],
                        )
                    ]
                )
            )
        delegated = next((m for m in reversed(messages) if m.get("role") == "tool"), {})
        return AgentAction(response_text=f"Delegated answer: {delegated.get('content')}")

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return "finalized"


class AgentAdvancedFeaturesTests(unittest.TestCase):
    def test_input_schema_validation_fails_fast(self) -> None:
        reg = ToolRegistry()
        agent = Agent(provider=_EchoFinalizeProvider(), tools=reg)
        output = agent.run_output(
            user_input="hello",
            input_schema={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
        )
        self.assertIn("Structured input validation", output.final_text)
        self.assertEqual(output.tool_results, [])

    def test_retry_exception_replans(self) -> None:
        reg = ToolRegistry()
        seen = {"called": 0}

        def flaky() -> str:
            seen["called"] += 1
            if seen["called"] == 1:
                raise RetryAgentRun("Use safer parameters")
            return "ok"

        reg.register_tool(ToolSpec(name="ops.flaky", description=""), flaky)
        agent = Agent(provider=_OneCallThenTextProvider("ops.flaky", text="recovered"), tools=reg)
        text = agent.run_loop("start", max_rounds=3)
        self.assertEqual(text, "recovered")
        self.assertEqual(seen["called"], 1)

    def test_stop_exception_pauses_and_continue_run(self) -> None:
        reg = ToolRegistry()
        pause_seen = {"called": 0}

        def blocker() -> str:
            pause_seen["called"] += 1
            raise StopAgentRun("Need confirmation")

        reg.register_tool(ToolSpec(name="ops.blocker", description=""), blocker)
        agent = Agent(provider=_OneCallThenTextProvider("ops.blocker", text="continued"), tools=reg)
        paused = agent.run_output("go", max_rounds=2)
        self.assertTrue(paused.paused)
        self.assertEqual(paused.pause_reason, "Need confirmation")

        resumed = agent.continue_run(run_output=paused, max_rounds=2)
        self.assertFalse(resumed.paused)
        self.assertEqual(resumed.final_text, "continued")

    def test_continue_run_finally_preserves_original_exception(self) -> None:
        reg = ToolRegistry()

        def blocker() -> str:
            raise StopAgentRun("Need confirmation")

        reg.register_tool(ToolSpec(name="ops.blocker", description=""), blocker)
        agent = Agent(provider=_OneCallThenTextProvider("ops.blocker", text="continued"), tools=reg)
        paused = agent.run_output("go", max_rounds=2)
        self.assertTrue(paused.paused)

        agent.provider = _ExplodingProvider()
        with self.assertRaisesRegex(RuntimeError, "boom"):
            agent.continue_run(run_output=paused, max_rounds=2)
        self.assertNotIn(paused.run_id, agent._active_runs)

    def test_output_model_pipeline(self) -> None:
        reg = ToolRegistry()
        agent = Agent(provider=_ConstantFinalizeProvider("draft"), tools=reg)
        output_model = _ConstantFinalizeProvider("polished")
        parser_model = _ConstantFinalizeProvider('{"value": 2}')
        out = agent.run_output(
            "x",
            output_model=output_model,
            parser_model=parser_model,
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(out.final_text, '{"value": 2}')
        self.assertEqual(out.output["value"], 2)

    def test_add_and_set_tools(self) -> None:
        reg = ToolRegistry()
        reg.register_toolkit_loader("calculator", CalculatorToolkit())
        provider = _OneCallThenTextProvider("ping", text="ok")
        agent = Agent(provider=provider, tools=reg)

        def ping() -> str:
            return "pong"

        agent.add_tool(ping)
        names = [tool.name for tool in reg.list_tools()]
        self.assertIn("ping", names)

        def pong() -> str:
            return "ping"

        agent.set_tools([RegisteredTool(spec=ToolSpec(name="pong", description=""), handler=pong)])
        names = [tool.name for tool in reg.list_tools()]
        self.assertEqual(names, ["pong"])

    def test_orchestrator_mode_delegates_to_member_agent(self) -> None:
        member = Agent(provider=_ConstantFinalizeProvider("42"), tools=ToolRegistry(), mode="member")
        orchestrator_registry = ToolRegistry()
        orchestrator = Agent(
            provider=_DelegatingProvider("agent.member.math"),
            tools=orchestrator_registry,
            mode="orchestration",
            members={"math": member},
        )

        result = orchestrator.run_loop("delegate this", max_rounds=3)
        self.assertEqual(result, "Delegated answer: 42")
        self.assertIn("agent.member.math", [tool.name for tool in orchestrator_registry.list_tools()])

    def test_invalid_mode_and_member_name_validation(self) -> None:
        with self.assertRaises(ValueError):
            Agent(provider=_ConstantFinalizeProvider("x"), tools=ToolRegistry(), mode="invalid-mode")

        member = Agent(provider=_ConstantFinalizeProvider("ok"), tools=ToolRegistry(), mode="member")
        with self.assertRaises(ValueError):
            Agent(
                provider=_ConstantFinalizeProvider("x"),
                tools=ToolRegistry(),
                mode="orchestration",
                members={"bad name": member},
            )


class AgentAdvancedFeaturesAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_arun_loop_orchestrator_mode_delegates_to_member_agent(self) -> None:
        member = Agent(provider=_ConstantFinalizeProvider("42"), tools=ToolRegistry(), mode="member")
        orchestrator = Agent(
            provider=_DelegatingProvider("agent.member.math"),
            tools=ToolRegistry(),
            mode="delegator",
            members={"math": member},
        )

        result = await orchestrator.arun_loop("delegate async", max_rounds=3)
        self.assertEqual(result, "Delegated answer: 42")


if __name__ == "__main__":
    unittest.main()
