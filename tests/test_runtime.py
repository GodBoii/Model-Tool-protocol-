from __future__ import annotations

import asyncio
import pytest
from typing import Any

from mtp.runtime import (
    ExecutionCancelledError,
    RegisteredTool,
    ToolRegistry,
    ToolRetryError,
    ToolStopError,
    _CacheEntry,
)
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from mtp.policy import PolicyDecision, RiskPolicy
from mtp.exceptions import RetryAgentRun, StopAgentRun

from conftest import make_tool_spec, make_echo_handler, make_call, make_plan, make_add_handler


class TestToolRegistry:
    def test_register_and_list(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        tools = reg.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "test.echo"

    def test_duplicate_register_raises(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        with pytest.raises(ValueError, match="already registered"):
            reg.register_tool(spec, make_echo_handler())

    def test_unregister(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        assert reg.unregister_tool("test.echo") is True
        assert reg.list_tools() == []

    def test_unregister_nonexistent(self):
        reg = ToolRegistry()
        assert reg.unregister_tool("nope") is False

    def test_set_tools(self):
        reg = ToolRegistry()
        reg.register_tool(make_tool_spec("a"), make_echo_handler())
        reg.set_tools([RegisteredTool(spec=make_tool_spec("b"), handler=make_echo_handler())])
        names = {s.name for s in reg.list_tools()}
        assert names == {"b"}

    def test_add_tool(self):
        reg = ToolRegistry()
        tool = RegisteredTool(spec=make_tool_spec("test.add"), handler=make_add_handler())
        reg.add_tool(tool)
        assert len(reg.list_tools()) == 1

    def test_list_tools_caches(self):
        reg = ToolRegistry()
        reg.register_tool(make_tool_spec("a"), make_echo_handler())
        list1 = reg.list_tools()
        list2 = reg.list_tools()
        assert list1 == list2

    def test_list_tools_includes_loader_preview(self):
        from mtp.toolkits.calculator import CalculatorToolkit
        reg = ToolRegistry()
        reg.register_toolkit_loader("calculator", CalculatorToolkit())
        specs = reg.list_tools()
        names = {s.name for s in specs}
        assert "calculator.add" in names
        assert "calculator.subtract" in names

    def test_register_tool_invalidate_cache(self):
        reg = ToolRegistry()
        reg.register_tool(make_tool_spec("a"), make_echo_handler())
        list1 = reg.list_tools()
        reg.register_tool(make_tool_spec("b"), make_echo_handler())
        list2 = reg.list_tools()
        assert len(list2) == 2


class TestLazyLoading:
    def test_lazy_load_on_execute(self):
        from mtp.toolkits.calculator import CalculatorToolkit
        reg = ToolRegistry()
        reg.register_toolkit_loader("calculator", CalculatorToolkit())
        assert "calculator.add" not in {t.spec.name for t in reg._tools.values()}
        reg.ensure_tools_available(["calculator.add"])
        assert "calculator.add" in {t.spec.name for t in reg._tools.values()}

    def test_ensure_tools_available_no_dot(self):
        reg = ToolRegistry()
        reg.register_tool(make_tool_spec("echo"), make_echo_handler())
        reg.ensure_tools_available(["echo"])
        assert "echo" in {t.spec.name for t in reg._tools.values()}

    def test_ensure_tools_available_missing_no_loader(self):
        reg = ToolRegistry()
        reg.ensure_tools_available(["nonexistent.tool"])
        assert "nonexistent.tool" not in {t.spec.name for t in reg._tools.values()}


class TestCacheEntry:
    def test_valid_when_not_expired(self):
        from datetime import UTC, datetime, timedelta
        entry = _CacheEntry(value="v", expires_at=datetime.now(UTC) + timedelta(seconds=60))
        assert entry.valid() is True

    def test_invalid_when_expired(self):
        from datetime import UTC, datetime, timedelta
        entry = _CacheEntry(value="v", expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert entry.valid() is False


@pytest.mark.asyncio
class TestExecuteCall:
    async def test_basic_execution(self, registry_with_echo, echo_spec, echo_handler):
        reg = registry_with_echo
        call = make_call(args={"text": "hello"})
        result = await reg.execute_call(call, {})
        assert result.success is True
        assert result.output == "hello"
        assert result.call_id == "c1"

    async def test_unknown_tool(self):
        reg = ToolRegistry()
        call = make_call(name="nonexistent.tool")
        result = await reg.execute_call(call, {})
        assert result.success is False
        assert "Unknown tool" in result.error

    async def test_handler_exception(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.fail")
        reg.register_tool(spec, lambda text: (_ for _ in ()).throw(ValueError("boom")))
        call = make_call(name="test.fail")
        result = await reg.execute_call(call, {})
        assert result.success is False
        assert "boom" in result.error

    async def test_invalid_arguments(self):
        reg = ToolRegistry()
        spec = make_tool_spec("test.strict", input_schema={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
            "additionalProperties": False,
        })
        reg.register_tool(spec, lambda x: x)
        call = make_call(name="test.strict", args={"wrong": "field"})
        result = await reg.execute_call(call, {})
        assert result.success is False
        assert "Invalid" in result.error

    async def test_schema_coercion_for_native_tool_args(self):
        reg = ToolRegistry()
        spec = make_tool_spec(
            "test.math",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "integer"},
                    "ok": {"type": "boolean"},
                },
                "required": ["a", "b", "ok"],
                "additionalProperties": False,
            },
        )
        reg.register_tool(spec, lambda a, b, ok: {"a": a, "b": b, "ok": ok})
        call = make_call(name="test.math", args={"a": "18", "b": "6", "ok": "true"})
        result = await reg.execute_call(call, {})
        assert result.success is True
        assert result.output == {"a": 18, "b": 6, "ok": True}

    async def test_missing_ref_fails_cleanly(self):
        reg = ToolRegistry()
        spec = make_tool_spec(
            "test.math_ref",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {
                        "anyOf": [
                            {"type": "number"},
                            {
                                "type": "object",
                                "properties": {"$ref": {"type": "string"}},
                                "required": ["$ref"],
                                "additionalProperties": False,
                            },
                        ]
                    },
                },
                "required": ["a", "b"],
                "additionalProperties": False,
            },
        )
        reg.register_tool(spec, lambda a, b: a)
        call = make_call(name="test.math_ref", args={"a": "4", "b": "{\"$ref\":\"call_1\"}"})
        result = await reg.execute_call(call, {})
        assert result.success is False
        assert "Missing tool result reference" in result.error

    async def test_retry_exception(self):
        reg = ToolRegistry()
        def retry_fn(text: str):
            raise RetryAgentRun("retry please")
        reg.register_tool(make_tool_spec("test.retry"), retry_fn)
        call = make_call(name="test.retry")
        with pytest.raises(ToolRetryError, match="retry please"):
            await reg.execute_call(call, {})

    async def test_stop_exception(self):
        reg = ToolRegistry()
        def stop_fn(text: str):
            raise StopAgentRun("stop please")
        reg.register_tool(make_tool_spec("test.stop"), stop_fn)
        call = make_call(name="test.stop")
        with pytest.raises(ToolStopError, match="stop please"):
            await reg.execute_call(call, {})


@pytest.mark.asyncio
class TestExecutePlan:
    async def test_sequential_execution(self, registry_with_echo):
        reg = registry_with_echo
        plan = make_plan(
            make_call("c1", args={"text": "first"}),
            make_call("c2", args={"text": "second"}),
            mode="sequential",
        )
        results = await reg.execute_plan(plan)
        assert len(results) == 2
        assert results[0].output == "first"
        assert results[1].output == "second"

    async def test_parallel_execution(self):
        reg = ToolRegistry()
        def slow_add(a: float, b: float) -> float:
            import time; time.sleep(0.1)
            return a + b
        spec = ToolSpec(
            name="math.add",
            description="Add",
            input_schema={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]},
        )
        reg.register_tool(spec, slow_add)
        plan = ExecutionPlan(
            batches=[ToolBatch(
                mode="parallel",
                calls=[
                    ToolCall(id="c1", name="math.add", arguments={"a": 1, "b": 2}),
                    ToolCall(id="c2", name="math.add", arguments={"a": 3, "b": 4}),
                ],
            )]
        )
        import time
        start = time.monotonic()
        results = await reg.execute_plan(plan)
        elapsed = time.monotonic() - start
        assert len(results) == 2
        assert elapsed < 0.35
        outputs = {r.call_id: r.output for r in results}
        assert outputs["c1"] == 3
        assert outputs["c2"] == 7

    async def test_ref_resolution(self):
        reg = ToolRegistry()
        def identity(x: Any) -> Any:
            return x
        spec = ToolSpec(
            name="test.identity",
            description="identity",
            input_schema={"type": "object", "properties": {"x": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "object"}]}}},
        )
        reg.register_tool(spec, identity)
        plan = ExecutionPlan(
            batches=[
                ToolBatch(mode="sequential", calls=[
                    ToolCall(id="c1", name="test.identity", arguments={"x": "hello"}),
                ]),
                ToolBatch(mode="sequential", calls=[
                    ToolCall(id="c2", name="test.identity", arguments={"x": {"$ref": "c1"}}),
                ]),
            ]
        )
        results = await reg.execute_plan(plan)
        assert len(results) == 2
        assert results[0].output == "hello"
        assert results[1].output == "hello"

    async def test_empty_plan(self):
        reg = ToolRegistry()
        results = await reg.execute_plan(ExecutionPlan())
        assert results == []

    async def test_cancel_before_execution(self):
        reg = ToolRegistry()
        reg.register_tool(make_tool_spec("test.echo"), make_echo_handler())
        plan = make_plan(make_call())
        with pytest.raises(ExecutionCancelledError):
            await reg.execute_plan(plan, cancel_checker=lambda: True)

    async def test_cancel_between_batches(self):
        reg = ToolRegistry()
        reg.register_tool(make_tool_spec("test.echo"), make_echo_handler())
        plan = ExecutionPlan(
            batches=[
                ToolBatch(mode="sequential", calls=[make_call("c1")]),
                ToolBatch(mode="sequential", calls=[make_call("c2")]),
            ]
        )
        call_count = 0
        def cancel_after_first():
            nonlocal call_count
            call_count += 1
            return call_count > 1
        with pytest.raises(ExecutionCancelledError):
            await reg.execute_plan(plan, cancel_checker=cancel_after_first)

    async def test_parallel_dedup(self):
        reg = ToolRegistry()
        call_count = 0
        def counting_echo(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return text
        reg.register_tool(make_tool_spec("test.echo"), counting_echo)
        plan = ExecutionPlan(
            batches=[ToolBatch(
                mode="parallel",
                calls=[
                    ToolCall(id="c1", name="test.echo", arguments={"text": "same"}),
                    ToolCall(id="c2", name="test.echo", arguments={"text": "same"}),
                ],
            )]
        )
        results = await reg.execute_plan(plan)
        assert call_count == 1
        assert len(results) == 2
        assert results[0].output == "same"
        assert results[1].output == "same"


@pytest.mark.asyncio
class TestCaching:
    async def test_cache_hit(self):
        reg = ToolRegistry()
        call_count = 0
        def counting_echo(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return text
        spec = make_tool_spec("test.echo", cache_ttl_seconds=60)
        reg.register_tool(spec, counting_echo)
        plan = make_plan(make_call("c1", args={"text": "cached"}))
        r1 = await reg.execute_plan(plan)
        assert call_count == 1
        r2 = await reg.execute_plan(plan)
        assert call_count == 1
        assert r2[0].cached is True

    async def test_cache_different_args(self):
        reg = ToolRegistry()
        call_count = 0
        def counting_echo(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return text
        spec = make_tool_spec("test.echo", cache_ttl_seconds=60)
        reg.register_tool(spec, counting_echo)
        plan1 = make_plan(make_call("c1", args={"text": "a"}))
        plan2 = make_plan(make_call("c1", args={"text": "b"}))
        await reg.execute_plan(plan1)
        await reg.execute_plan(plan2)
        assert call_count == 2


class TestApprovalPolicy:
    @pytest.mark.asyncio
    async def test_deny_policy(self):
        reg = ToolRegistry(policy=RiskPolicy(by_risk={ToolRiskLevel.READ_ONLY: PolicyDecision.DENY}))
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        call = make_call()
        result = await reg.execute_call(call, {})
        assert result.success is False
        assert "denied" in result.error
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_ask_policy_no_handler(self):
        reg = ToolRegistry(policy=RiskPolicy(by_risk={ToolRiskLevel.READ_ONLY: PolicyDecision.ASK}))
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        call = make_call()
        result = await reg.execute_call(call, {})
        assert result.success is False
        assert "approval" in result.error
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_ask_policy_approved(self):
        reg = ToolRegistry(
            policy=RiskPolicy(by_risk={ToolRiskLevel.READ_ONLY: PolicyDecision.ASK}),
            approval_handler=lambda spec, call, args: True,
        )
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        call = make_call()
        result = await reg.execute_call(call, {})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_ask_policy_denied_by_handler(self):
        reg = ToolRegistry(
            policy=RiskPolicy(by_risk={ToolRiskLevel.READ_ONLY: PolicyDecision.ASK}),
            approval_handler=lambda spec, call, args: False,
        )
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        call = make_call()
        result = await reg.execute_call(call, {})
        assert result.success is False
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_async_approval_handler(self):
        async def async_approve(spec, call, args):
            return True
        reg = ToolRegistry(
            policy=RiskPolicy(by_risk={ToolRiskLevel.READ_ONLY: PolicyDecision.ASK}),
            approval_handler=async_approve,
        )
        spec = make_tool_spec("test.echo")
        reg.register_tool(spec, make_echo_handler())
        call = make_call()
        result = await reg.execute_call(call, {})
        assert result.success is True
