"""The agent chooses an available native async final stream at its round limit."""

from __future__ import annotations

import asyncio

from mtp import (
    Agent,
    AgentAction,
    ExecutionPlan,
    ToolBatch,
    ToolCall,
    ToolRegistry,
    ToolSpec,
)
from mtp.providers.common import ProviderCapabilities


def test_agent_round_limit_uses_native_async_finalization():
    class Provider:
        def capabilities(self):
            return ProviderCapabilities(
                provider="fixture",
                supports_tool_calling=True,
                supports_parallel_tool_calls=True,
                input_modalities=["text"],
                supports_finalize_streaming=True,
                supports_native_async=True,
            )

        async def anext_action_stream(self, messages, tools):
            yield AgentAction(
                plan=ExecutionPlan(
                    batches=[
                        ToolBatch(
                            mode="sequential",
                            calls=[
                                ToolCall(
                                    id="call", name="echo", arguments={"value": 17}
                                )
                            ],
                        )
                    ]
                )
            )

        async def anext_action(self, messages, tools):
            async for action in self.anext_action_stream(messages, tools):
                return action

        def finalize_stream(self, messages, results):
            raise AssertionError("Synchronous finalization must not run.")

        async def afinalize_stream(self, messages, results):
            assert results[0].output == 17
            yield "done"

    registry = ToolRegistry()
    registry.register_tool(ToolSpec("echo", "echo"), lambda value: value)

    async def scenario():
        events = [
            event
            async for event in Agent(
                provider=Provider(), tools=registry
            ).arun_loop_events("run", max_rounds=1)
        ]
        assert events[-1]["type"] == "run_completed"
        assert events[-1]["final_text"] == "done"

    asyncio.run(scenario())


def test_closing_agent_stream_closes_async_only_finalizer():
    closed = []

    class Provider:
        def capabilities(self):
            return ProviderCapabilities(
                provider="fixture",
                supports_tool_calling=True,
                supports_parallel_tool_calls=True,
                input_modalities=["text"],
                supports_finalize_streaming=True,
                supports_native_async=True,
            )

        async def anext_action(self, messages, tools):
            return AgentAction(
                plan=ExecutionPlan(
                    batches=[
                        ToolBatch(
                            mode="sequential",
                            calls=[ToolCall(id="call", name="echo", arguments={})],
                        )
                    ]
                )
            )

        async def afinalize_stream(self, messages, results):
            try:
                yield "partial"
                await asyncio.sleep(30)
            finally:
                closed.append(True)

    registry = ToolRegistry()
    registry.register_tool(ToolSpec("echo", "echo"), lambda: 17)

    async def scenario():
        stream = Agent(provider=Provider(), tools=registry).arun_loop_events(
            "run", max_rounds=1
        )
        async for event in stream:
            if event["type"] == "text_chunk":
                await stream.aclose()
                break
        assert closed == [True]

    asyncio.run(scenario())
