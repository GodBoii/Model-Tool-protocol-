from __future__ import annotations

import json
import pathlib
import shutil
import sys
import unittest
from uuid import uuid4

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, JsonSessionStore, ToolRegistry
from mtp.agent import AgentAction, ProviderAdapter
from mtp.prompts import DEFAULT_AUTORESEARCH_SYSTEM_INSTRUCTIONS
from mtp.protocol import ToolResult, ToolSpec


class _HistoryEchoProvider(ProviderAdapter):
    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        user_texts = [str(m.get("content")) for m in messages if m.get("role") == "user"]
        return AgentAction(response_text=" || ".join(user_texts))

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return ""


class _CaptureProvider(ProviderAdapter):
    def __init__(self) -> None:
        self.last_messages: list[dict] = []
        self.last_tools: list[str] = []

    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        self.last_messages = [dict(message) for message in messages]
        self.last_tools = [tool.name for tool in tools]
        return AgentAction(response_text="ok")

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return "ok"


class SessionStoreTests(unittest.TestCase):
    def _workspace_temp_dir(self) -> pathlib.Path:
        root = pathlib.Path("tmp") / f"test_session_store_{uuid4().hex}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_json_session_store_restores_history_across_agent_instances(self) -> None:
        tmp_dir = self._workspace_temp_dir()
        try:
            store = JsonSessionStore(db_path=tmp_dir, session_table="sessions")
            tools = ToolRegistry()

            first_agent = Agent(provider=_HistoryEchoProvider(), tools=tools, session_store=store)
            first = first_agent.run_output("first message", session_id="s1", user_id="u1")
            self.assertIn("first message", first.final_text)

            second_agent = Agent(provider=_HistoryEchoProvider(), tools=tools, session_store=store)
            second = second_agent.run_output("second message", session_id="s1", user_id="u1")
            self.assertIn("first message", second.final_text)
            self.assertIn("second message", second.final_text)

            payload = json.loads(pathlib.Path(tmp_dir, "sessions.json").read_text(encoding="utf-8"))
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["session_id"], "s1")
            self.assertEqual(payload[0]["user_id"], "u1")
            self.assertGreaterEqual(len(payload[0]["runs"]), 2)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_session_history_is_isolated_by_user(self) -> None:
        tmp_dir = self._workspace_temp_dir()
        try:
            store = JsonSessionStore(db_path=tmp_dir, session_table="sessions")
            tools = ToolRegistry()

            first_agent = Agent(provider=_HistoryEchoProvider(), tools=tools, session_store=store)
            first_agent.run_output("user1 message", session_id="shared", user_id="u1")

            second_agent = Agent(provider=_HistoryEchoProvider(), tools=tools, session_store=store)
            second = second_agent.run_output("user2 message", session_id="shared", user_id="u2")
            self.assertNotIn("user1 message", second.final_text)
            self.assertIn("user2 message", second.final_text)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_autoresearch_content_not_reused_when_disabled(self) -> None:
        tmp_dir = self._workspace_temp_dir()
        try:
            store = JsonSessionStore(db_path=tmp_dir, session_table="sessions")
            tools = ToolRegistry()

            on_provider = _CaptureProvider()
            on_agent = Agent(
                provider=on_provider,
                tools=tools,
                session_store=store,
                autoresearch=True,
            )
            on_agent.run_output("first run", session_id="s1", user_id="u1")
            self.assertIn("agent.terminate", on_provider.last_tools)
            self.assertTrue(
                any(
                    DEFAULT_AUTORESEARCH_SYSTEM_INSTRUCTIONS in str(message.get("content", ""))
                    for message in on_provider.last_messages
                    if message.get("role") == "system"
                )
            )

            off_provider = _CaptureProvider()
            off_agent = Agent(
                provider=off_provider,
                tools=tools,
                session_store=store,
                autoresearch=False,
            )
            off_agent.run_output("second run", session_id="s1", user_id="u1")

            self.assertNotIn("agent.terminate", off_provider.last_tools)
            self.assertFalse(
                any(
                    DEFAULT_AUTORESEARCH_SYSTEM_INSTRUCTIONS in str(message.get("content", ""))
                    for message in off_provider.last_messages
                    if message.get("role") == "system"
                )
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
