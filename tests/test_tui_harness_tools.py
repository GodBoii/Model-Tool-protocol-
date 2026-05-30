from __future__ import annotations

import pathlib
import sys
import unittest

from tests.harness_utils import safe_rmtree, workspace_tempdir

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.cli.tui_harness_agent import build_orchestrator_instructions
from mtp.cli.tui_harness_policy import HarnessRiskPolicy
from mtp import Agent, AgentAction, ExecutionPlan, ToolBatch, ToolRegistry, ToolSpec
from mtp.cli.tui_harness_tools import CommandToolkit, ContextToolkit
from mtp.codebase import CodebaseMemory
from mtp.policy import PolicyDecision
from mtp.protocol import ToolCall
from mtp.runtime import RegisteredTool


def _handlers(root: pathlib.Path):
    return {tool.spec.name: tool.handler for tool in ContextToolkit(root).load_tools()}


class TUIHarnessToolsTests(unittest.TestCase):
    def test_search_returns_bounded_metadata_not_whole_files(self) -> None:
        tmp = workspace_tempdir(prefix="tui_harness_tools")
        try:
            src = tmp / "src"
            src.mkdir()
            (src / "app.py").write_text(
                "\n".join(
                    [
                        "def alpha():",
                        "    return 'needle one'",
                        "def beta():",
                        "    return 'needle two'",
                        "def gamma():",
                        "    return 'needle three'",
                    ]
                ),
                encoding="utf-8",
            )
            (tmp / "node_modules").mkdir()
            (tmp / "node_modules" / "ignored.py").write_text("needle from dependency", encoding="utf-8")
            tools = _handlers(tmp)

            result = tools["fs.search"]("needle", limit=2)

            self.assertEqual(result["count"], 1)
            self.assertFalse(result["truncated"])
            self.assertEqual(result["total_matches"], 1)
            self.assertEqual(result["hits"][0]["file"], "src/app.py")
            self.assertNotIn("dependency", "\n".join("\n".join(hit["snippets"]) for hit in result["hits"]))
        finally:
            safe_rmtree(tmp)

    def test_project_inspect_reports_root_structure_counts_and_git_status(self) -> None:
        tmp = workspace_tempdir(prefix="tui_harness_inspect")
        try:
            (tmp / "src").mkdir()
            (tmp / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
            (tmp / "web.js").write_text("console.log('hi')", encoding="utf-8")
            (tmp / "node_modules").mkdir()
            (tmp / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")
            tools = _handlers(tmp)

            result = tools["project.inspect"]()

            self.assertIn({"name": "src", "type": "directory"}, result["root_structure"])
            self.assertIn({"name": "node_modules", "type": "directory"}, result["root_structure"])
            self.assertEqual(result["file_counts"]["by_extension"][".py"], 1)
            self.assertEqual(result["file_counts"]["by_extension"][".js"], 1)
            self.assertNotIn("src/app.py", str(result["file_counts"]))
            self.assertIn("git_status", result)
        finally:
            safe_rmtree(tmp)

    def test_read_text_defaults_to_small_slice_and_reports_line_window(self) -> None:
        tmp = workspace_tempdir(prefix="tui_harness_read")
        try:
            (tmp / "large.txt").write_text("\n".join(f"line {i}" for i in range(1, 301)), encoding="utf-8")
            tools = _handlers(tmp)

            result = tools["fs.read_text"]("large.txt")
            window = tools["fs.read_text"]("large.txt", start_line=250, end_line=252)

            self.assertEqual(result["start_line"], 1)
            self.assertEqual(result["end_line"], 240)
            self.assertEqual(result["total_lines"], 300)
            self.assertTrue(result["truncated"])
            self.assertEqual(window["content"], "line 250\nline 251\nline 252")
        finally:
            safe_rmtree(tmp)

    def test_secret_files_are_not_searched_or_read(self) -> None:
        tmp = workspace_tempdir(prefix="tui_harness_secret")
        try:
            (tmp / ".env").write_text("TOKEN=needle", encoding="utf-8")
            (tmp / "safe.txt").write_text("needle", encoding="utf-8")
            tools = _handlers(tmp)

            result = tools["fs.search"]("needle", limit=10)

            self.assertEqual([hit["file"] for hit in result["hits"]], ["safe.txt"])
            with self.assertRaises(ValueError):
                tools["fs.read_text"](".env")
        finally:
            safe_rmtree(tmp)

    def test_orchestrator_instructions_explain_tools_and_delegation(self) -> None:
        instructions = build_orchestrator_instructions(cwd=pathlib.Path("C:/work/project"), mode="code")

        self.assertIn("Tool guide:", instructions)
        self.assertIn("Delegation:", instructions)
        self.assertIn("agent.explore_codebase: read-only explorer", instructions)
        self.assertIn("agent.debug_context: read-only debug gatherer", instructions)
        self.assertIn("agent.syntax_check: read-only Python syntax checker", instructions)
        self.assertIn("project.inspect gives root structure", instructions)
        self.assertIn("fs.search finds relevant relative file paths", instructions)
        self.assertIn("Search first, read narrowly", instructions)
        self.assertNotIn("codebase.search", instructions)

    def test_syntax_check_subagent_is_treated_as_read_only_by_policy(self) -> None:
        spec = next(tool.spec for tool in CommandToolkit(pathlib.Path.cwd()).load_tools() if tool.spec.name == "agent.syntax_check")
        decision = HarnessRiskPolicy(mode="plan").decide(
            spec,
            ToolCall(id="syntax", name="agent.syntax_check", arguments={"path": "src"}),
            {"path": "src"},
        )

        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_search_finds_files_by_path_word_and_fuzzy_text(self) -> None:
        tmp = workspace_tempdir(prefix="tui_harness_search")
        try:
            (tmp / "package.json").write_text("{}", encoding="utf-8")
            (tmp / "src").mkdir()
            (tmp / "src" / "version.ts").write_text("export const version = '1.0.0'", encoding="utf-8")
            (tmp / "src" / "auth.py").write_text("def authenticate_user():\n    return True", encoding="utf-8")
            tools = _handlers(tmp)

            version_matches = tools["fs.search"](query="version", limit=10)
            fuzzy_matches = tools["fs.search"](query="auth user", limit=10)

            self.assertEqual(version_matches["hits"][0]["file"], "src/version.ts")
            self.assertEqual(fuzzy_matches["hits"][0]["file"], "src/auth.py")
        finally:
            safe_rmtree(tmp)

    def test_search_accepts_pattern_alias(self) -> None:
        tmp = workspace_tempdir(prefix="tui_harness_pattern_alias")
        try:
            (tmp / "src").mkdir()
            (tmp / "src" / "spinner_widget.py").write_text("class SpinnerWidget:\n    pass\n", encoding="utf-8")
            tools = _handlers(tmp)

            result = tools["fs.search"](pattern="spinner widget", limit=10)
            grep_result = tools["fs.grep"](pattern="spinner", limit=10)

            self.assertEqual(result["hits"][0]["file"], "src/spinner_widget.py")
            self.assertEqual(grep_result["hits"][0]["file"], "src/spinner_widget.py")
        finally:
            safe_rmtree(tmp)

    def test_search_uses_codebase_memory_when_enabled(self) -> None:
        tmp = workspace_tempdir(prefix="tui_harness_memory")
        try:
            (tmp / "src").mkdir()
            (tmp / "src" / "memory_target.py").write_text(
                "class ConversationSummary:\n    pass\n",
                encoding="utf-8",
            )
            CodebaseMemory(tmp).scan(enable=True)
            tools = _handlers(tmp)

            result = tools["fs.search"](query="conversation summary", limit=5)
            inspect_result = tools["project.inspect"]()

            self.assertEqual(result["source"], "codebase_memory")
            self.assertEqual(result["hits"][0]["file"], "src/memory_target.py")
            self.assertTrue(inspect_result["codebase_memory"]["enabled"])
            self.assertGreater(inspect_result["codebase_memory"]["chunks"], 0)
        finally:
            safe_rmtree(tmp)

    def test_memory_show_reports_stored_breakdown(self) -> None:
        tmp = workspace_tempdir(prefix="tui_harness_memory_show")
        try:
            (tmp / "src").mkdir()
            (tmp / "src" / "memory_target.py").write_text(
                "class ConversationSummary:\n    pass\n",
                encoding="utf-8",
            )
            memory = CodebaseMemory(tmp)
            memory.scan(enable=True)
            memory.record_conversation_summary(
                session_id="session-1",
                prompt="summarize the conversation",
                response="created a class and explained it",
                backend="codex",
                model="gpt-test",
            )

            data = memory.show(limit=4)

            self.assertTrue(data["enabled"])
            self.assertGreaterEqual(data["files"], 1)
            self.assertGreaterEqual(data["chunks"], 1)
            self.assertGreaterEqual(data["summaries"], 1)
            self.assertTrue(data["languages"])
            self.assertTrue(data["chunk_kinds"])
            self.assertTrue(data["recent_summaries"])
        finally:
            safe_rmtree(tmp)

    def test_repeated_tool_plan_reuses_results_without_reexecuting(self) -> None:
        class RepeatingProvider:
            def __init__(self) -> None:
                self.rounds = 0

            def next_action(self, messages, tools):
                self.rounds += 1
                if self.rounds > 3:
                    return AgentAction(response_text="done")
                return AgentAction(
                    plan=ExecutionPlan(
                        batches=[
                            ToolBatch(
                                mode="parallel",
                                calls=[
                                    ToolCall(id=f"c{i}", name="project.inspect", arguments={})
                                    for i in range(20)
                                ],
                            )
                        ]
                    )
                )

            def finalize(self, messages, tool_results):
                return "final"

        reg = ToolRegistry()
        calls = {"count": 0}

        def inspect():
            calls["count"] += 1
            return {"ok": True}

        reg.add_tool(RegisteredTool(ToolSpec("project.inspect", "inspect", cache_ttl_seconds=30), inspect))
        agent = Agent(provider=RepeatingProvider(), tools=reg)

        events = list(agent.run_loop_events("go", max_rounds=5, stream_final=False, stream_tool_results=False))
        started = [event for event in events if event.get("type") == "tool_started"]
        trimmed = [event for event in events if event.get("type") == "tool_plan_trimmed"]

        self.assertEqual(calls["count"], 1)
        self.assertEqual(len(started), 1)
        self.assertTrue(trimmed)


if __name__ == "__main__":
    unittest.main()
