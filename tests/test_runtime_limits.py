from __future__ import annotations

import asyncio

import pytest

from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolSpec
from mtp.runtime import ToolRegistry


def _call(call_id: str, *, depends_on: list[str] | None = None) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="test.work",
        arguments={"call_id": call_id},
        depends_on=depends_on or [],
    )


def test_registry_rejects_invalid_concurrency_limit() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        ToolRegistry(max_concurrency=0)


@pytest.mark.asyncio
async def test_parallel_plan_respects_concurrency_limit() -> None:
    registry = ToolRegistry(max_concurrency=2)
    active = 0
    peak = 0

    async def work(call_id: str) -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return call_id

    registry.register_tool(
        ToolSpec(
            name="test.work",
            description="Test worker",
            input_schema={
                "type": "object",
                "properties": {"call_id": {"type": "string"}},
                "required": ["call_id"],
            },
        ),
        work,
    )
    plan = ExecutionPlan(
        batches=[ToolBatch(mode="parallel", calls=[_call(str(index)) for index in range(8)])]
    )

    results = await registry.execute_plan(plan)

    assert peak == 2
    assert [result.output for result in results] == [str(index) for index in range(8)]


@pytest.mark.asyncio
async def test_failed_dependency_skips_downstream_call() -> None:
    registry = ToolRegistry()
    executed: list[str] = []

    async def work(call_id: str) -> str:
        executed.append(call_id)
        if call_id == "first":
            raise RuntimeError("boom")
        return call_id

    registry.register_tool(
        ToolSpec(
            name="test.work",
            description="Test worker",
            input_schema={
                "type": "object",
                "properties": {"call_id": {"type": "string"}},
                "required": ["call_id"],
            },
        ),
        work,
    )
    plan = ExecutionPlan(
        batches=[
            ToolBatch(mode="sequential", calls=[_call("first"), _call("second", depends_on=["first"])])
        ]
    )

    results = await registry.execute_plan(plan)

    assert executed == ["first"]
    assert results[0].success is False
    assert results[1].success is False
    assert results[1].skipped is True
    assert "first" in (results[1].error or "")
