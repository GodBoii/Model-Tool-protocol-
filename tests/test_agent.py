from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, MTPAgent, ToolRegistry
from mtp.agent import AgentAction, ProviderAdapter
from mtp.providers import MockPlannerProvider
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult
from mtp.runtime import RegisteredTool, ToolkitLoader
from mtp.protocol import ToolRiskLevel, ToolSpec


class GitHubToolkit(ToolkitLoader):
    def load_tools(self) -> list[RegisteredTool]:
        def get_user() -> dict:
            return {"login": "alice"}

        def create_issue(title: str, body: dict) -> str:
            return f"{title}:{body['login']}"

        return [
            RegisteredTool(
                spec=ToolSpec(
                    name="github.get_user",
                    description="",
                    risk_level=ToolRiskLevel.READ_ONLY,
                ),
                handler=get_user,
            ),
            RegisteredTool(
                spec=ToolSpec(
                    name="github.create_issue",
                    description="",
                    risk_level=ToolRiskLevel.WRITE,
                ),
                handler=create_issue,
            ),
        ]


class _DirectResponseProvider(ProviderAdapter):
    def __init__(self, text: str = "ok") -> None:
        self.text = text

    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        return AgentAction(response_text=self.text)

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return self.text


class _PlanThenRespondProvider(ProviderAdapter):
    def __init__(self, *, reasoning: str | None = None) -> None:
        self._step = 0
        self.reasoning = reasoning

    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        if self._step == 0:
            self._step += 1
            return AgentAction(
                plan=ExecutionPlan(
                    batches=[
                        ToolBatch(
                            mode="parallel",
                            calls=[
                                ToolCall(
                                    id="call-1",
                                    name="echo.tool",
                                    arguments={"text": "hello"},
                                    reasoning=self.reasoning,
                                )
                            ],
                        )
                    ]
                ),
                metadata={
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "echo.tool", "arguments": "{\"text\":\"hello\"}"},
                                "reasoning": self.reasoning,
                            }
                        ],
                    }
                },
            )
        return AgentAction(response_text="done")

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return "done"


class _PlanWithReasoningArgProvider(ProviderAdapter):
    def __init__(self) -> None:
        self._step = 0
        self.last_tools: list[ToolSpec] = []

    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        self.last_tools = list(tools)
        if self._step == 0:
            self._step += 1
            return AgentAction(
                plan=ExecutionPlan(
                    batches=[
                        ToolBatch(
                            mode="parallel",
                            calls=[
                                ToolCall(
                                    id="call-1",
                                    name="echo.tool",
                                    arguments={"text": "hello", "reasoning": "Need the echoed value for the reply"},
                                )
                            ],
                        )
                    ]
                )
            )
        return AgentAction(response_text="done")

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return "done"


class _PlanWithGenericAndPerCallReasoningProvider(ProviderAdapter):
    def __init__(self) -> None:
        self._step = 0

    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        if self._step == 0:
            self._step += 1
            return AgentAction(
                plan=ExecutionPlan(
                    batches=[
                        ToolBatch(
                            mode="parallel",
                            calls=[
                                ToolCall(
                                    id="call-1",
                                    name="echo.tool",
                                    arguments={"text": "hello", "reasoning": "Per-call: run echo first"},
                                    reasoning="Generic: I will solve the task step by step",
                                )
                            ],
                        )
                    ]
                )
            )
        return AgentAction(response_text="done")

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return "done"


class _FailingProvider(ProviderAdapter):
    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        raise RuntimeError("boom")

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return "never"


class AgentTests(unittest.TestCase):
    def test_mock_planner_flow(self) -> None:
        reg = ToolRegistry()
        reg.register_toolkit_loader("github", GitHubToolkit())
        agent = Agent(provider=MockPlannerProvider(), registry=reg)
        response = agent.run("use profile and create issue")
        self.assertIn("Issue created", response)

    def test_tools_alias_for_registry(self) -> None:
        reg = ToolRegistry()
        reg.register_toolkit_loader("github", GitHubToolkit())
        agent = Agent(provider=MockPlannerProvider(), tools=reg)
        response = agent.run("use profile and create issue")
        self.assertIn("Issue created", response)

    def test_rejects_mismatched_tools_and_registry(self) -> None:
        reg_a = ToolRegistry()
        reg_b = ToolRegistry()
        with self.assertRaises(ValueError):
            Agent(provider=MockPlannerProvider(), tools=reg_a, registry=reg_b)

    def test_mtpagent_accepts_tools_alias(self) -> None:
        reg = ToolRegistry()
        reg.register_toolkit_loader("github", GitHubToolkit())
        agent = MTPAgent(provider=MockPlannerProvider(), tools=reg)
        self.assertIs(agent._agent.tools, reg)

    def test_run_events_include_system_instructions(self) -> None:
        reg = ToolRegistry()
        agent = Agent(
            provider=_DirectResponseProvider("hi"),
            tools=reg,
            system_instructions="System prompt",
            instructions="User instructions",
        )
        first_event = next(agent.run_loop_events("hello", max_rounds=1, stream_final=False))
        self.assertEqual(first_event["type"], "run_started")
        self.assertIn("System prompt", first_event["system_instructions"])
        self.assertIn("User instructions", first_event["user_instructions"])

    def test_run_events_include_member_agents(self) -> None:
        member_registry = ToolRegistry()
        member_registry.register_tool(ToolSpec(name="calc.add", description=""), lambda a, b: a + b)
        member = Agent(provider=_DirectResponseProvider("member-ok"), tools=member_registry, mode="member")
        reg = ToolRegistry()
        agent = Agent(
            provider=_DirectResponseProvider("ok"),
            tools=reg,
            mode="orchestration",
            members={"calculator": member},
        )
        first_event = next(agent.run_loop_events("hello", max_rounds=1, stream_final=False))
        self.assertEqual(first_event["type"], "run_started")
        member_agents = first_event["member_agents"]
        self.assertEqual(len(member_agents), 1)
        self.assertEqual(member_agents[0]["id"], "calculator")
        self.assertEqual(member_agents[0]["mode"], "member")
        self.assertIn("calc.add", member_agents[0]["tools"])
        self.assertIn("system_instructions", member_agents[0])
        self.assertIn("user_instructions", member_agents[0])
        self.assertIn("orchestration_instructions", member_agents[0])

    def test_print_response_stream_events_pretty_format(self) -> None:
        member_registry = ToolRegistry()
        member_registry.register_tool(ToolSpec(name="calc.add", description=""), lambda a, b: a + b)
        member = Agent(provider=_DirectResponseProvider("member-ok"), tools=member_registry, mode="member")
        reg = ToolRegistry()
        agent = MTPAgent(
            provider=_DirectResponseProvider("hello world"),
            tools=reg,
            mode="orchestration",
            members={"calculator": member},
            debug_mode=True,
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            agent.print_response("hello", max_rounds=1, stream=True, stream_events=True)
        printed = buffer.getvalue()
        self.assertIn("[MTP RUN START]", printed)
        self.assertIn("Tools:", printed)
        self.assertIn("Sub Agents:", printed)
        self.assertIn("id=calculator", printed)
        self.assertIn("calc.add", printed)
        self.assertIn("System Instructions:", printed)
        self.assertIn("User Instructions:", printed)
        self.assertIn("Orchestration Instructions:", printed)
        self.assertIn("User Message:", printed)
        self.assertIn("[MTP RUN END]", printed)

    def test_print_response_stream_events_normal_mode_hides_debug_events(self) -> None:
        reg = ToolRegistry()
        reg.register_tool(ToolSpec(name="echo.tool", description=""), lambda text: f"echo:{text}")
        agent = MTPAgent(provider=_PlanThenRespondProvider(), tools=reg, debug_mode=False)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            agent.print_response("hello", max_rounds=2, stream=True, stream_events=True)
        printed = buffer.getvalue()
        self.assertIn("Mode            : normal", printed)
        self.assertNotIn("[MTP PLAN]", printed)
        self.assertNotIn("[MTP TOOL START]", printed)
        self.assertNotIn("[MTP TOOL END]", printed)
        self.assertIn("[MTP RUN END]", printed)

    def test_print_response_stream_events_debug_mode_shows_detailed_events(self) -> None:
        reg = ToolRegistry()
        reg.register_tool(ToolSpec(name="echo.tool", description=""), lambda text: f"echo:{text}")
        agent = MTPAgent(provider=_PlanThenRespondProvider(), tools=reg, debug_mode=True)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            agent.print_response("hello", max_rounds=2, stream=True, stream_events=True)
        printed = buffer.getvalue()
        self.assertIn("[MTP PLAN]", printed)
        self.assertIn("[MTP TOOL START]", printed)
        self.assertIn("[MTP TOOL END]", printed)
        self.assertIn("METRICS", printed)
        self.assertIn("<tools>", printed)
        self.assertIn("<system_instructions>", printed)
        self.assertNotIn("<llm_response>", printed)
        self.assertNotIn("<instruction>", printed)

    def test_run_events_emit_run_failed_and_pretty_prints_error(self) -> None:
        reg = ToolRegistry()
        agent = MTPAgent(provider=_FailingProvider(), tools=reg, debug_mode=False)
        buffer = io.StringIO()
        with self.assertRaises(RuntimeError):
            with contextlib.redirect_stdout(buffer):
                agent.print_response("hello", max_rounds=1, stream=True, stream_events=True)
        printed = buffer.getvalue()
        self.assertIn("[MTP RUN FAILED]", printed)
        self.assertIn("Error:", printed)
        self.assertIn("boom", printed)

    def test_run_events_can_disable_tool_events(self) -> None:
        reg = ToolRegistry()
        reg.register_tool(ToolSpec(name="echo.tool", description=""), lambda text: f"echo:{text}")
        agent = Agent(provider=_PlanThenRespondProvider(), tools=reg, stream_tool_events=False)
        event_types = [event["type"] for event in agent.run_loop_events("hello", max_rounds=2, stream_final=False)]
        self.assertNotIn("assistant_tool_message", event_types)
        self.assertNotIn("batch_started", event_types)
        self.assertNotIn("tool_started", event_types)
        self.assertNotIn("tool_finished", event_types)

    def test_tool_finished_omits_payload_when_stream_tool_results_disabled(self) -> None:
        reg = ToolRegistry()
        reg.register_tool(ToolSpec(name="echo.tool", description=""), lambda text: f"echo:{text}")
        agent = Agent(
            provider=_PlanThenRespondProvider(reasoning="Need to run tool first"),
            tools=reg,
            stream_tool_events=True,
            stream_tool_results=False,
        )
        events = list(agent.run_loop_events("hello", max_rounds=2, stream_final=False))
        tool_started = next(event for event in events if event["type"] == "tool_started")
        self.assertEqual(tool_started.get("reasoning"), "Need to run tool first")
        tool_finished = next(event for event in events if event["type"] == "tool_finished")
        self.assertIsNone(tool_finished.get("output"))
        self.assertIsNone(tool_finished.get("error"))
        self.assertEqual(tool_finished.get("reasoning"), "Need to run tool first")

    def test_tool_finished_includes_payload_when_stream_tool_results_enabled(self) -> None:
        reg = ToolRegistry()
        reg.register_tool(ToolSpec(name="echo.tool", description=""), lambda text: f"echo:{text}")
        agent = Agent(
            provider=_PlanThenRespondProvider(reasoning="Need to run tool first"),
            tools=reg,
            stream_tool_events=True,
            stream_tool_results=True,
        )
        events = list(agent.run_loop_events("hello", max_rounds=2, stream_final=False))
        tool_finished = next(event for event in events if event["type"] == "tool_finished")
        self.assertEqual(tool_finished.get("output"), "echo:hello")

    def test_reasoning_argument_is_extracted_when_not_in_tool_schema(self) -> None:
        reg = ToolRegistry()
        reg.register_tool(ToolSpec(name="echo.tool", description=""), lambda text: f"echo:{text}")
        provider = _PlanWithReasoningArgProvider()
        agent = Agent(provider=provider, tools=reg, stream_tool_events=True, stream_tool_results=True)
        events = list(agent.run_loop_events("hello", max_rounds=2, stream_final=False))
        tool_started = next(event for event in events if event["type"] == "tool_started")
        self.assertEqual(tool_started.get("reasoning"), "Need the echoed value for the reply")
        tool_finished = next(event for event in events if event["type"] == "tool_finished")
        self.assertEqual(tool_finished.get("output"), "echo:hello")

    def test_provider_tool_schema_includes_optional_reasoning(self) -> None:
        reg = ToolRegistry()
        reg.register_tool(ToolSpec(name="echo.tool", description=""), lambda text: f"echo:{text}")
        provider = _PlanWithReasoningArgProvider()
        agent = Agent(provider=provider, tools=reg)
        agent.run_loop("hello", max_rounds=2)
        echo_spec = next(spec for spec in provider.last_tools if spec.name == "echo.tool")
        schema = echo_spec.input_schema
        self.assertIsInstance(schema, dict)
        props = schema.get("properties", {})
        self.assertIsInstance(props, dict)
        self.assertIn("reasoning", props)

    def test_per_call_reasoning_overrides_generic_reasoning(self) -> None:
        reg = ToolRegistry()
        reg.register_tool(ToolSpec(name="echo.tool", description=""), lambda text: f"echo:{text}")
        provider = _PlanWithGenericAndPerCallReasoningProvider()
        agent = Agent(provider=provider, tools=reg, stream_tool_events=True, stream_tool_results=False)
        events = list(agent.run_loop_events("hello", max_rounds=2, stream_final=False))
        tool_started = next(event for event in events if event["type"] == "tool_started")
        self.assertEqual(tool_started.get("reasoning"), "Per-call: run echo first")


if __name__ == "__main__":
    unittest.main()
