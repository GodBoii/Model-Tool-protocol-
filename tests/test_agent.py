from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, MTPAgent, ToolRegistry
from mtp.providers import MockPlannerProvider
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


if __name__ == "__main__":
    unittest.main()
