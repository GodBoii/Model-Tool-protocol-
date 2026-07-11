from __future__ import annotations

import asyncio
import threading
import time

import pytest

from mtp.protocol import ToolCall, ToolSpec
from mtp.runtime import ToolRegistry


def _spec() -> ToolSpec:
    return ToolSpec(name="test.slow", description="Slow", input_schema={"type": "object"})


def test_registry_validates_tool_timeout() -> None:
    with pytest.raises(ValueError, match="tool_timeout_seconds"):
        ToolRegistry(tool_timeout_seconds=0)


@pytest.mark.asyncio
async def test_async_tool_timeout_returns_structured_failure() -> None:
    registry = ToolRegistry(tool_timeout_seconds=0.01)

    async def slow() -> None:
        await asyncio.sleep(1)

    registry.register_tool(_spec(), slow)
    result = await registry.execute_call(ToolCall(id="1", name="test.slow"), {})

    assert result.success is False
    assert "exceeded" in (result.error or "")


@pytest.mark.asyncio
async def test_sync_tool_timeout_signals_cooperative_cancellation() -> None:
    registry = ToolRegistry(tool_timeout_seconds=0.02)
    stopped = threading.Event()

    def slow(cancel_event: threading.Event) -> None:
        while not cancel_event.is_set():
            time.sleep(0.002)
        stopped.set()

    registry.register_tool(_spec(), slow)
    result = await registry.execute_call(ToolCall(id="1", name="test.slow"), {})

    assert result.success is False
    assert stopped.wait(0.2)
