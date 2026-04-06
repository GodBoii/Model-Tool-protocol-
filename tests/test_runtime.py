from __future__ import annotations

import asyncio
import pathlib
import sys
import time
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolRiskLevel, ToolSpec
from mtp.runtime import ExecutionCancelledError, RegisteredTool, ToolRegistry, ToolkitLoader
from mtp.policy import PolicyDecision, RiskPolicy


class DemoToolkit(ToolkitLoader):
    def __init__(self) -> None:
        self.loaded = False

    def load_tools(self) -> list[RegisteredTool]:
        self.loaded = True

        def ping() -> str:
            return "pong"

        return [
            RegisteredTool(
                spec=ToolSpec(
                    name="github.ping",
                    description="Ping tool",
                    risk_level=ToolRiskLevel.READ_ONLY,
                ),
                handler=ping,
            )
        ]


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_lazy_loading(self) -> None:
        reg = ToolRegistry()
        toolkit = DemoToolkit()
        reg.register_toolkit_loader("github", toolkit)
        result = await reg.execute_call(ToolCall(id="1", name="github.ping"), {})
        self.assertTrue(toolkit.loaded)
        self.assertTrue(result.success)
        self.assertEqual(result.output, "pong")

    async def test_dependency_reference(self) -> None:
        reg = ToolRegistry()

        def base() -> dict:
            return {"x": 41}

        def plus_one(payload: dict) -> int:
            return payload["x"] + 1

        reg.register_tool(
            ToolSpec(name="math.base", description="Base value"),
            base,
        )
        reg.register_tool(
            ToolSpec(name="math.plus_one", description="Increment"),
            plus_one,
        )

        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(id="c1", name="math.base"),
                        ToolCall(
                            id="c2",
                            name="math.plus_one",
                            arguments={"payload": {"$ref": "c1"}},
                            depends_on=["c1"],
                        ),
                    ],
                )
            ]
        )

        results = await reg.execute_plan(plan)
        self.assertEqual(results[-1].output, 42)

    async def test_parallel_batch(self) -> None:
        reg = ToolRegistry()

        async def slow_one() -> int:
            await asyncio.sleep(0.2)
            return 1

        async def slow_two() -> int:
            await asyncio.sleep(0.2)
            return 2

        reg.register_tool(ToolSpec(name="t.one", description=""), slow_one)
        reg.register_tool(ToolSpec(name="t.two", description=""), slow_two)

        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="parallel",
                    calls=[
                        ToolCall(id="a", name="t.one"),
                        ToolCall(id="b", name="t.two"),
                    ],
                )
            ]
        )
        start = time.perf_counter()
        results = await reg.execute_plan(plan)
        elapsed = time.perf_counter() - start
        self.assertEqual(len(results), 2)
        self.assertLess(elapsed, 0.35)

    async def test_cache_ttl(self) -> None:
        reg = ToolRegistry()
        calls = {"count": 0}

        def expensive(x: int) -> int:
            calls["count"] += 1
            return x * 10

        reg.register_tool(
            ToolSpec(
                name="math.expensive",
                description="",
                cache_ttl_seconds=60,
            ),
            expensive,
        )

        first = await reg.execute_call(
            ToolCall(id="1", name="math.expensive", arguments={"x": 3}),
            {},
        )
        second = await reg.execute_call(
            ToolCall(id="2", name="math.expensive", arguments={"x": 3}),
            {},
        )

        self.assertEqual(first.output, 30)
        self.assertEqual(second.output, 30)
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(calls["count"], 1)

    async def test_input_schema_validation(self) -> None:
        reg = ToolRegistry()

        def plus(a: int, b: int) -> int:
            return a + b

        reg.register_tool(
            ToolSpec(
                name="math.plus",
                description="",
                input_schema={
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
            ),
            plus,
        )

        result = await reg.execute_call(ToolCall(id="1", name="math.plus", arguments={"a": "1"}), {})
        self.assertFalse(result.success)
        self.assertIn("Invalid tool arguments", result.error or "")

    async def test_ask_policy_can_be_approved(self) -> None:
        policy = RiskPolicy(by_tool_name={"ops.delete": PolicyDecision.ASK})
        reg = ToolRegistry(policy=policy, approval_handler=lambda _spec, _call, _args: True)
        reg.register_tool(
            ToolSpec(
                name="ops.delete",
                description="",
                risk_level=ToolRiskLevel.DESTRUCTIVE,
            ),
            lambda path: f"deleted:{path}",
        )

        result = await reg.execute_call(ToolCall(id="1", name="ops.delete", arguments={"path": "x"}), {})
        self.assertTrue(result.success)
        self.assertEqual(result.output, "deleted:x")

    async def test_execute_plan_can_be_cancelled(self) -> None:
        reg = ToolRegistry()

        reg.register_tool(ToolSpec(name="t.one", description=""), lambda: 1)
        reg.register_tool(ToolSpec(name="t.two", description=""), lambda: 2)

        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(id="a", name="t.one"),
                        ToolCall(id="b", name="t.two"),
                    ],
                )
            ]
        )

        with self.assertRaises(ExecutionCancelledError):
            await reg.execute_plan(plan, cancel_checker=lambda: True)

    async def test_in_flight_cancellation_async_tool(self) -> None:
        reg = ToolRegistry()

        async def slow() -> str:
            await asyncio.sleep(2)
            return "done"

        reg.register_tool(ToolSpec(name="t.slow", description=""), slow)
        plan = ExecutionPlan(batches=[ToolBatch(mode="sequential", calls=[ToolCall(id="s1", name="t.slow")])])

        start = time.perf_counter()
        cancel_flag = {"stop": False}

        async def flip_cancel() -> None:
            await asyncio.sleep(0.1)
            cancel_flag["stop"] = True

        flipper = asyncio.create_task(flip_cancel())
        with self.assertRaises(ExecutionCancelledError):
            await reg.execute_plan(plan, cancel_checker=lambda: cancel_flag["stop"])
        await flipper
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1.0)

    async def test_in_flight_cancellation_sync_cooperative_tool(self) -> None:
        reg = ToolRegistry()

        def cooperative(cancel_event: threading.Event | None = None) -> str:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return "cancelled"
                time.sleep(0.01)

        reg.register_tool(ToolSpec(name="t.coop", description=""), cooperative)
        plan = ExecutionPlan(batches=[ToolBatch(mode="sequential", calls=[ToolCall(id="c1", name="t.coop")])])

        start = time.perf_counter()
        cancel_flag = {"stop": False}

        async def flip_cancel() -> None:
            await asyncio.sleep(0.1)
            cancel_flag["stop"] = True

        flipper = asyncio.create_task(flip_cancel())
        with self.assertRaises(ExecutionCancelledError):
            await reg.execute_plan(plan, cancel_checker=lambda: cancel_flag["stop"])
        await flipper
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
