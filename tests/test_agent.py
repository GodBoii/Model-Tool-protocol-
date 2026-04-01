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
from mtp.protocol import ToolResult
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
        self.assertNotIn("system_instructions", member_agents[0])

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


if __name__ == "__main__":
    unittest.main()
