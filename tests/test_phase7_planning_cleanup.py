"""An event consumer owns the lifetime of the provider's planning stream."""

from __future__ import annotations

import asyncio

import pytest

from mtp.agent import Agent, AgentAction
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolSpec
from mtp.runtime import ToolRegistry


@pytest.mark.parametrize("chunk_type", ["text_chunk", "reasoning_chunk"])
@pytest.mark.parametrize("completed_action", [False, True])
def test_closing_agent_event_consumer_closes_planning_stream_immediately(
    chunk_type, completed_action
):
    class Provider:
        closed = False
        continued = False

        async def astream_next_action(self, messages, tools):
            try:
                if completed_action:
                    yield AgentAction(
                        plan=ExecutionPlan(
                            batches=[
                                ToolBatch(
                                    mode="sequential",
                                    calls=[
                                        ToolCall(id="c1", name="echo", arguments={})
                                    ],
                                )
                            ]
                        )
                    )
                yield {"type": chunk_type, "chunk": "partial"}
                self.continued = True
                pytest.fail("Closed event consumer continued reading provider output.")
            finally:
                self.closed = True

    async def run():
        provider = Provider()
        registry = ToolRegistry()
        executions = []
        registry.register_tool(
            ToolSpec("echo", "echo"), lambda: executions.append("executed")
        )
        agent = Agent(provider=provider, tools=registry)
        events = agent.arun_loop_events("echo", max_rounds=1, stream_final=False)
        async for event in events:
            if event["type"] == chunk_type:
                assert event["chunk"] == "partial"
                break
        await events.aclose()
        assert provider.closed and not provider.continued
        assert executions == []

    asyncio.run(run())


def test_cancelling_agent_awaiting_planning_chunk_closes_stream():
    class Provider:
        closed = False
        started = None

        async def astream_next_action(self, messages, tools):
            self.started.set()
            try:
                await asyncio.Event().wait()
                yield AgentAction(response_text="unreachable")
            finally:
                self.closed = True

    async def run():
        provider = Provider()
        provider.started = asyncio.Event()
        agent = Agent(provider=provider, tools=ToolRegistry())

        async def consume():
            return [
                event
                async for event in agent.arun_loop_events("echo", stream_final=False)
            ]

        task = asyncio.create_task(consume())
        await provider.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.closed

    asyncio.run(run())
