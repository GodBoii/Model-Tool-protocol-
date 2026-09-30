from __future__ import annotations

import asyncio
import pytest
from typing import Any
from collections.abc import Iterator

from mtp.simple_agent import MTPAgent
from mtp.agent import Agent, AgentAction, RunOutput
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult, ToolSpec
from mtp.runtime import ToolRegistry, RegisteredTool
from mtp.providers.common import ProviderCapabilities, USAGE_METRICS_NONE

from conftest import _TextOnlyProvider, _ConstantActionProvider, make_tool_spec, make_echo_handler, make_call, make_plan


class TestMTPAgentInit:
    def test_requires_tools(self):
        with pytest.raises(ValueError, match="Missing tools"):
            MTPAgent(provider=_TextOnlyProvider(), tools=None)

    def test_conflicting_raises(self):
        with pytest.raises(ValueError, match="only one"):
            MTPAgent(provider=_TextOnlyProvider(), tools=ToolRegistry(), registry=ToolRegistry())

    def test_basic_init(self):
        reg = ToolRegistry()
        agent = MTPAgent(provider=_TextOnlyProvider(), tools=reg)
        assert isinstance(agent._agent, Agent)

    def test_debug_mode(self):
        reg = ToolRegistry()
        agent = MTPAgent(provider=_TextOnlyProvider(), tools=reg, debug_mode=True)
        assert agent._agent.debug_mode is True


class TestMTPAgentRun:
    def test_text_response(self):
        reg = ToolRegistry()
        agent = MTPAgent(provider=_TextOnlyProvider("Hello MTP"), tools=reg)
        result = agent.run("hi")
        assert result == "Hello MTP"

    def test_with_tool(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        plan = make_plan(make_call("c1", args={"text": "tool hello"}))
        provider = _ConstantActionProvider(AgentAction(plan=plan))
        agent = MTPAgent(provider=provider, tools=reg)
        result = agent.run("echo")
        assert "tool hello" in result


class TestMTPAgentRunOutput:
    def test_returns_run_output(self):
        reg = ToolRegistry()
        agent = MTPAgent(provider=_TextOnlyProvider("Output test"), tools=reg)
        output = agent.run_output("hi")
        assert isinstance(output, RunOutput)
        assert output.final_text == "Output test"

    def test_with_metadata(self):
        reg = ToolRegistry()
        agent = MTPAgent(provider=_TextOnlyProvider("Meta"), tools=reg)
        output = agent.run_output("hi", metadata={"source": "test"})
        assert output.metadata.get("source") == "test"


class TestMTPAgentRunEvents:
    def test_events(self):
        reg = ToolRegistry()
        agent = MTPAgent(provider=_TextOnlyProvider("Events"), tools=reg)
        events = list(agent.run_events("hi"))
        types = [e["type"] for e in events]
        assert "run_started" in types
        assert "run_completed" in types

    def test_with_tool_events(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        plan = make_plan(make_call("c1", args={"text": "event hello"}))
        provider = _ConstantActionProvider(AgentAction(plan=plan))
        agent = MTPAgent(provider=provider, tools=reg)
        events = list(agent.run_events("echo"))
        types = [e["type"] for e in events]
        assert "tool_finished" in types


class TestMTPAgentContinueRun:
    def test_continue(self):
        reg = ToolRegistry()
        agent = MTPAgent(provider=_TextOnlyProvider("Continued"), tools=reg)
        output = agent.run_output("test")
        assert isinstance(output, RunOutput)


@pytest.mark.asyncio
class TestMTPAgentAsync:
    async def test_arun(self):
        reg = ToolRegistry()
        agent = MTPAgent(provider=_TextOnlyProvider("Async MTP"), tools=reg)
        result = await agent.arun("hi")
        assert result == "Async MTP"

    async def test_arun_output(self):
        reg = ToolRegistry()
        agent = MTPAgent(provider=_TextOnlyProvider("Async Output"), tools=reg)
        output = await agent.arun_output("hi")
        assert isinstance(output, RunOutput)
        assert output.final_text == "Async Output"

    async def test_arun_events(self):
        reg = ToolRegistry()
        agent = MTPAgent(provider=_TextOnlyProvider("Async Events"), tools=reg)
        events = []
        async for event in agent.arun_events("hi"):
            events.append(event)
        types = [e["type"] for e in events]
        assert "run_started" in types
        assert "run_completed" in types


class TestMTPAgentMembers:
    def test_orchestrator_with_member(self):
        member_reg = ToolRegistry()
        member = MTPAgent(provider=_TextOnlyProvider("member result"), tools=member_reg, mode="member")
        main_reg = ToolRegistry()
        main = MTPAgent(
            provider=_TextOnlyProvider("orchestrator"),
            tools=main_reg,
            mode="orchestration",
            members={"worker": member._agent},
        )
        assert "worker" in main._agent.members


class TestMTPAgentConvenienceAliases:
    def test_agent_has_mtp_agent(self):
        assert hasattr(Agent, "MTPAgent")
        assert Agent.MTPAgent is MTPAgent

    def test_agent_has_tool_registry(self):
        assert hasattr(Agent, "ToolRegistry")
        assert Agent.ToolRegistry is ToolRegistry

    def test_agent_has_mtp_tool(self):
        assert hasattr(Agent, "mtp_tool")
        assert callable(Agent.mtp_tool)
