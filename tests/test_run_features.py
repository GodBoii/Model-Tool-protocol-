from __future__ import annotations

import pathlib
import sys
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, ToolBatch, ToolCall, ToolRegistry, ToolSpec
from mtp.agent import AgentAction, ProviderAdapter
from mtp.protocol import ExecutionPlan, ToolResult


class _PlanThenFinalizeProvider(ProviderAdapter):
    def __init__(self) -> None:
        self.calls = 0

    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        self.calls += 1
        if self.calls == 1:
            return AgentAction(
                plan=ExecutionPlan(
                    batches=[
                        ToolBatch(
                            mode="sequential",
                            calls=[ToolCall(id="c1", name="math.add", arguments={"a": 20, "b": 22})],
                        )
                    ]
                )
            )
        return AgentAction(response_text='{"answer": 42}')

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return '{"answer": 42}'


class _TwoSequentialCallsProvider(ProviderAdapter):
    def next_action(self, messages: list[dict], tools: list[ToolSpec]) -> AgentAction:
        return AgentAction(
            plan=ExecutionPlan(
                batches=[
                    ToolBatch(
                        mode="sequential",
                        calls=[
                            ToolCall(id="c1", name="ops.wait"),
                            ToolCall(id="c2", name="ops.after_wait"),
                        ],
                    )
                ]
            )
        )

    def finalize(self, messages: list[dict], tool_results: list[ToolResult]) -> str:
        return "done"


class RunFeaturesTests(unittest.TestCase):
    def test_run_output_contains_context_and_schema_valid_output(self) -> None:
        reg = ToolRegistry()
        reg.register_tool(ToolSpec(name="math.add", description=""), lambda a, b: a + b)
        agent = Agent(provider=_PlanThenFinalizeProvider(), tools=reg)
        output = agent.run_output(
            "compute",
            user_id="u1",
            session_id="s1",
            metadata={"source": "test"},
            output_schema={
                "type": "object",
                "properties": {"answer": {"type": "integer"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(output.user_id, "u1")
        self.assertEqual(output.session_id, "s1")
        self.assertEqual(output.metadata["source"], "test")
        self.assertEqual(output.output["answer"], 42)
        self.assertIsNone(output.output_validation_error)
        self.assertEqual(output.total_tool_calls, 1)

    def test_tool_call_limit_prevents_tool_execution(self) -> None:
        reg = ToolRegistry()
        calls = {"count": 0}

        def add(a: int, b: int) -> int:
            calls["count"] += 1
            return a + b

        reg.register_tool(ToolSpec(name="math.add", description=""), add)
        agent = Agent(provider=_PlanThenFinalizeProvider(), tools=reg)
        text = agent.run_loop("compute", max_rounds=3, tool_call_limit=0)
        self.assertEqual(calls["count"], 0)
        self.assertIn("42", text)

    def test_cancel_run_stops_before_second_call(self) -> None:
        reg = ToolRegistry()
        calls = {"after_wait": 0}

        def wait() -> str:
            time.sleep(0.15)
            return "ok"

        def after_wait() -> str:
            calls["after_wait"] += 1
            return "done"

        reg.register_tool(ToolSpec(name="ops.wait", description=""), wait)
        reg.register_tool(ToolSpec(name="ops.after_wait", description=""), after_wait)
        agent = Agent(provider=_TwoSequentialCallsProvider(), tools=reg)

        box: dict[str, str] = {}
        run_id = "run-cancel-test"

        # Force deterministic run id via structured API.
        thread = threading.Thread(
            target=lambda: box.setdefault(
                "text",
                agent.run_output("go", run_id=run_id, max_rounds=1, tool_call_limit=10).final_text,
            )
        )
        thread.start()
        cancelled = False
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not cancelled:
            cancelled = agent.cancel_run(run_id)
            if not cancelled:
                time.sleep(0.01)
        thread.join(timeout=2)

        self.assertTrue(cancelled)
        self.assertEqual(calls["after_wait"], 0)
        self.assertEqual(box.get("text"), "Run cancelled.")


if __name__ == "__main__":
    unittest.main()
