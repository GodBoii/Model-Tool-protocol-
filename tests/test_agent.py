from __future__ import annotations

import asyncio
import pytest
from typing import Any
from collections.abc import Iterator

from mtp.agent import Agent, AgentAction, RunOutput
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult, ToolSpec
from mtp.runtime import ToolRegistry, RegisteredTool, ToolRetryError, ToolStopError
from mtp.exceptions import RetryAgentRun, StopAgentRun
from mtp.providers.common import ProviderCapabilities, USAGE_METRICS_NONE
from mtp.session_store import JsonSessionStore, SessionRecord

from conftest import (
    _ConstantActionProvider,
    _TextOnlyProvider,
    _MultiRoundProvider,
    make_tool_spec,
    make_echo_handler,
    make_call,
    make_plan,
)


class TestAgentInit:
    def test_requires_tools(self):
        with pytest.raises(ValueError, match="Missing tools"):
            Agent(provider=_TextOnlyProvider(), registry=None)

    def test_conflicting_tools_raises(self):
        r1 = ToolRegistry()
        r2 = ToolRegistry()
        with pytest.raises(ValueError, match="only one"):
            Agent(provider=_TextOnlyProvider(), registry=r1, tools=r2)

    def test_tools_alias(self):
        reg = ToolRegistry()
        agent = Agent(provider=_TextOnlyProvider(), tools=reg)
        assert agent.tools is reg
        assert agent.registry is reg

    def test_registry_alias(self):
        reg = ToolRegistry()
        agent = Agent(provider=_TextOnlyProvider(), registry=reg)
        assert agent.tools is reg

    def test_default_mode(self):
        reg = ToolRegistry()
        agent = Agent(provider=_TextOnlyProvider(), tools=reg)
        assert agent.mode == "standalone"

    def test_invalid_mode_raises(self):
        reg = ToolRegistry()
        with pytest.raises(ValueError, match="mode"):
            Agent(provider=_TextOnlyProvider(), tools=reg, mode="invalid")

    def test_orchestrator_modes(self):
        reg = ToolRegistry()
        agent = Agent(provider=_TextOnlyProvider(), tools=reg, mode="orchestration")
        assert agent.mode == "orchestration"

    def test_debug_output_untruncated_by_default(self):
        reg = ToolRegistry()
        agent = Agent(provider=_TextOnlyProvider(), tools=reg)
        payload = "x" * 2000
        assert agent._short(payload) == payload

    def test_debug_output_can_still_be_limited(self):
        reg = ToolRegistry()
        agent = Agent(provider=_TextOnlyProvider(), tools=reg, debug_max_chars=10)
        payload = "x" * 50
        assert agent._short(payload).endswith("...<truncated>")


class TestAgentRunLoop:
    def test_text_only_response(self):
        reg = ToolRegistry()
        provider = _TextOnlyProvider("Hello world")
        agent = Agent(provider=provider, tools=reg)
        result = agent.run_loop("hi")
        assert result == "Hello world"

    def test_tool_execution(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        plan = make_plan(make_call("c1", args={"text": "hello"}))
        provider = _ConstantActionProvider(AgentAction(plan=plan))
        agent = Agent(provider=provider, tools=reg)
        result = agent.run_loop("echo hello")
        assert "hello" in result

    def test_multi_round_execution(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        plan1 = make_plan(make_call("c1", args={"text": "round1"}))
        plan2 = make_plan(make_call("c2", args={"text": "round2"}))
        provider = _MultiRoundProvider(
            [AgentAction(plan=plan1), AgentAction(plan=plan2)],
            final_text="Done.",
        )
        agent = Agent(provider=provider, tools=reg)
        result = agent.run_loop("multi round")
        assert "Done." in result

    def test_max_rounds_respected(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        plan = make_plan(make_call("c1", args={"text": "loop"}))
        provider = _MultiRoundProvider(
            [AgentAction(plan=plan)] * 100,
            final_text="Should not reach",
        )
        agent = Agent(provider=provider, tools=reg)
        result = agent.run_loop("loop", max_rounds=2)
        assert result is not None

    def test_tool_call_limit(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        plan = make_plan(
            make_call("c1", args={"text": "a"}),
            make_call("c2", args={"text": "b"}),
            make_call("c3", args={"text": "c"}),
            mode="sequential",
        )
        provider = _ConstantActionProvider(AgentAction(plan=plan))
        agent = Agent(provider=provider, tools=reg)
        result = agent.run_loop("test", tool_call_limit=2)
        assert result is not None

    def test_messages_accumulate(self):
        reg = ToolRegistry()
        provider = _TextOnlyProvider("ok")
        agent = Agent(provider=provider, tools=reg)
        agent.run_loop("first")
        agent.run_loop("second")
        assert len(agent.messages) >= 4


class TestAgentRunLoopEvents:
    def test_events_emitted(self):
        reg = ToolRegistry()
        provider = _TextOnlyProvider("Hello")
        agent = Agent(provider=provider, tools=reg)
        events = list(agent.run_loop_events("hi"))
        types = [e["type"] for e in events]
        assert "run_started" in types
        assert "run_completed" in types

    def test_text_chunk_events(self):
        reg = ToolRegistry()
        provider = _TextOnlyProvider("Hello world response")
        agent = Agent(provider=provider, tools=reg)
        events = list(agent.run_loop_events("hi"))
        text_chunks = [e for e in events if e["type"] == "text_chunk"]
        assert len(text_chunks) > 0

    def test_tool_events(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        plan = make_plan(make_call("c1", args={"text": "hello"}))
        provider = _ConstantActionProvider(AgentAction(plan=plan))
        agent = Agent(provider=provider, tools=reg, stream_tool_events=True, stream_tool_results=True)
        events = list(agent.run_loop_events("echo"))
        types = [e["type"] for e in events]
        assert "tool_started" in types
        assert "tool_finished" in types


class TestAgentRunOutput:
    def test_returns_run_output(self):
        reg = ToolRegistry()
        provider = _TextOnlyProvider("Hello")
        agent = Agent(provider=provider, tools=reg)
        output = agent.run_output("hi")
        assert isinstance(output, RunOutput)
        assert output.final_text == "Hello"
        assert isinstance(output.run_id, str)
        assert output.cancelled is False

    def test_with_tool_execution(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        plan = make_plan(make_call("c1", args={"text": "world"}))
        provider = _ConstantActionProvider(AgentAction(plan=plan))
        agent = Agent(provider=provider, tools=reg)
        output = agent.run_output("echo world")
        assert "world" in output.final_text
        assert output.total_tool_calls >= 1


class TestAgentToolManagement:
    def test_add_tool(self):
        reg = ToolRegistry()
        agent = Agent(provider=_TextOnlyProvider(), tools=reg)
        tool = RegisteredTool(spec=make_tool_spec("test.new"), handler=make_echo_handler())
        agent.add_tool(tool)
        names = {s.name for s in reg.list_tools()}
        assert "test.new" in names

    def test_set_tools(self):
        reg = ToolRegistry()
        reg.register_tool(make_tool_spec("old"), make_echo_handler())
        agent = Agent(provider=_TextOnlyProvider(), tools=reg)
        new_spec = make_tool_spec("new")
        agent.set_tools([RegisteredTool(spec=new_spec, handler=make_echo_handler())])
        names = {s.name for s in reg.list_tools()}
        assert "new" in names
        assert "old" not in names


class TestAgentMembers:
    def test_add_member(self):
        reg = ToolRegistry()
        member = Agent(provider=_TextOnlyProvider(), tools=ToolRegistry(), mode="member")
        agent = Agent(provider=_TextOnlyProvider(), tools=reg, mode="orchestration")
        agent.add_member("calculator", member)
        assert "calculator" in agent.members

    def test_add_member_standalone_still_adds(self):
        reg = ToolRegistry()
        member = Agent(provider=_TextOnlyProvider(), tools=ToolRegistry(), mode="member")
        agent = Agent(provider=_TextOnlyProvider(), tools=reg, mode="standalone")
        agent.add_member("calc", member)
        assert "calc" in agent.members

    def test_add_member_duplicate_raises(self):
        reg = ToolRegistry()
        member = Agent(provider=_TextOnlyProvider(), tools=ToolRegistry(), mode="member")
        agent = Agent(provider=_TextOnlyProvider(), tools=reg, mode="orchestration")
        agent.add_member("calc", member)
        with pytest.raises(ValueError, match="already registered"):
            agent.add_member("calc", member)


class TestAgentContinueRun:
    def test_continue_run(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())

        class PauseProvider:
            def __init__(self):
                self._round = 0
            def next_action(self, messages, tools):
                self._round += 1
                if self._round == 1:
                    plan = make_plan(make_call("c1", args={"text": "first"}))
                    return AgentAction(plan=plan)
                return AgentAction(response_text="Continued after pause")
            def finalize(self, messages, tool_results):
                return "Continued after pause"
            def finalize_stream(self, messages, tool_results):
                yield "Continued after pause"
            async def anext_action(self, messages, tools):
                return self.next_action(messages, tools)
            async def afinalize(self, messages, tool_results):
                return self.finalize(messages, tool_results)
            def capabilities(self):
                return ProviderCapabilities(provider="pause_test", usage_metrics_quality=USAGE_METRICS_NONE)

        provider = PauseProvider()
        agent = Agent(provider=provider, tools=reg)
        output = agent.run_output("test")
        assert isinstance(output, RunOutput)


class TestAgentRetryStop:
    def test_retry_exception_triggers_replanning(self):
        call_count = 0
        def retry_once(text: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RetryAgentRun("try again")
            return "success"

        reg = ToolRegistry()
        spec = make_tool_spec("test.retry")
        reg.register_tool(spec, retry_once)
        plan = make_plan(make_call("c1", name="test.retry", args={"text": "go"}))
        provider = _ConstantActionProvider(AgentAction(plan=plan))
        agent = Agent(provider=provider, tools=reg)
        result = agent.run_loop("test")
        assert call_count >= 2

    def test_stop_exception_pauses(self):
        def stopper(text: str) -> str:
            raise StopAgentRun("need approval")

        reg = ToolRegistry()
        spec = make_tool_spec("test.stop")
        reg.register_tool(spec, stopper)
        plan = make_plan(make_call("c1", name="test.stop", args={"text": "go"}))
        provider = _ConstantActionProvider(AgentAction(plan=plan))
        agent = Agent(provider=provider, tools=reg)
        output = agent.run_output("test")
        assert output.paused is True


class TestAgentSession:
    def test_session_save(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        reg = ToolRegistry()
        provider = _TextOnlyProvider("Hello session")
        agent = Agent(provider=provider, tools=reg, session_store=store)
        output = agent.run_output("test", session_id="s1", user_id="u1")
        assert output.session_id == "s1"
        session = store.get_session("s1", user_id="u1")
        assert session is not None
        assert len(session.runs) >= 1

    def test_session_restores_messages(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        store.upsert_session(SessionRecord(
            session_id="s1",
            messages=[{"role": "user", "content": "previous message"}],
        ))
        reg = ToolRegistry()
        provider = _TextOnlyProvider("Hello again")
        agent = Agent(provider=provider, tools=reg, session_store=store)
        agent.run_output("new message", session_id="s1")
        session = store.get_session("s1")
        assert any(m.get("content") == "previous message" for m in session.messages)


class TestAgentStrictDependency:
    def test_strict_violation_feedback(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        plan = ExecutionPlan(
            batches=[ToolBatch(
                mode="sequential",
                calls=[ToolCall(
                    id="c1",
                    name="test.echo",
                    arguments={"text": {"$ref": "c0"}},
                    depends_on=[],
                )],
            )]
        )
        provider = _ConstantActionProvider(AgentAction(plan=plan))
        agent = Agent(provider=provider, tools=reg, strict_dependency_mode=True)
        result = agent.run_loop("test")
        assert result is not None


@pytest.mark.asyncio
class TestAgentAsync:
    async def test_arun_loop(self):
        reg = ToolRegistry()
        provider = _TextOnlyProvider("Async hello")
        agent = Agent(provider=provider, tools=reg)
        result = await agent.arun_loop("hi")
        assert result == "Async hello"

    async def test_arun_output(self):
        reg = ToolRegistry()
        provider = _TextOnlyProvider("Async output")
        agent = Agent(provider=provider, tools=reg)
        output = await agent.arun_output("hi")
        assert isinstance(output, RunOutput)
        assert output.final_text == "Async output"

    async def test_arun_loop_events(self):
        reg = ToolRegistry()
        provider = _TextOnlyProvider("Async events")
        agent = Agent(provider=provider, tools=reg)
        events = []
        async for event in agent.arun_loop_events("hi"):
            events.append(event)
        types = [e["type"] for e in events]
        assert "run_started" in types
        assert "run_completed" in types

    async def test_async_tool_execution(self):
        reg = ToolRegistry()
        async def async_echo(text: str) -> str:
            await asyncio.sleep(0.01)
            return text
        spec = make_tool_spec("test.async_echo")
        reg.register_tool(spec, async_echo)
        plan = make_plan(make_call("c1", name="test.async_echo", args={"text": "async hello"}))
        provider = _ConstantActionProvider(AgentAction(plan=plan))
        agent = Agent(provider=provider, tools=reg)
        result = await agent.arun_loop("test")
        assert "async hello" in result


class TestAgentInputSchema:
    def test_input_schema_validated(self):
        reg = ToolRegistry()
        provider = _TextOnlyProvider("ok")
        agent = Agent(provider=provider, tools=reg)
        output = agent.run_output(
            {"query": "test"},
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        assert output.final_text == "ok"

    def test_input_schema_validation_failure(self):
        reg = ToolRegistry()
        provider = _TextOnlyProvider("ok")
        agent = Agent(provider=provider, tools=reg)
        output = agent.run_output(
            {"wrong": "field"},
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        assert output.output_validation_error is not None
