from __future__ import annotations

import pytest
from mtp.providers.simple_planner import SimplePlannerProvider
from mtp.providers.mock import MockPlannerProvider
from mtp.protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from mtp.providers.common import ProviderCapabilities
from mtp.providers.common import openai_like_tool_call_plan_payload


class TestSimplePlannerProvider:
    def test_is_mock_alias(self):
        assert MockPlannerProvider is SimplePlannerProvider

    def test_capabilities(self):
        p = SimplePlannerProvider()
        caps = p.capabilities()
        assert isinstance(caps, ProviderCapabilities)
        assert caps.provider == "simple_planner"
        assert caps.supports_tool_calling is True

    def test_profile_triggers_plan(self):
        p = SimplePlannerProvider()
        messages = [{"role": "user", "content": "show my profile"}]
        action = p.next_action(messages, [])
        assert action.plan is not None
        assert len(action.plan.batches) == 1
        calls = action.plan.batches[0].calls
        assert calls[0].name == "github.get_user"
        assert calls[1].name == "github.create_issue"

    def test_non_profile_returns_text(self):
        p = SimplePlannerProvider()
        messages = [{"role": "user", "content": "hello world"}]
        action = p.next_action(messages, [])
        assert action.plan is None
        assert action.response_text is not None

    def test_finalize_with_success(self):
        p = SimplePlannerProvider()
        results = [ToolResult(call_id="c1", tool_name="test", output="ok")]
        text = p.finalize([], results)
        assert "Ran" in text or "Done" in text

    def test_finalize_with_failure(self):
        p = SimplePlannerProvider()
        results = [ToolResult(call_id="c1", tool_name="test", output=None, success=False, error="boom")]
        text = p.finalize([], results)
        assert "failed" in text.lower()

    def test_finalize_with_issue_created(self):
        p = SimplePlannerProvider()
        results = [ToolResult(call_id="c1", tool_name="github.create_issue", output="issue-42")]
        text = p.finalize([], results)
        assert "issue-42" in text

    def test_capabilities_returns_correct_provider(self):
        p = SimplePlannerProvider()
        caps = p.capabilities()
        assert caps.supports_finalize_streaming is False
        assert caps.supports_native_async is False

    def test_finalize_returns_string(self):
        p = SimplePlannerProvider()
        results = [ToolResult(call_id="c1", tool_name="test", output="ok")]
        text = p.finalize([], results)
        assert isinstance(text, str)


class TestProviderTranslationHelpers:
    def test_openai_like_helper_builds_plan_and_metadata(self):
        class Function:
            name = "math.add"
            arguments = '{"a": 1, "b": 2}'

        class ToolCallObj:
            id = "call_0"
            function = Function()

        payload = openai_like_tool_call_plan_payload(
            provider="unit",
            model="model-x",
            tool_calls=[ToolCallObj()],
            content="using a tool",
            tool_call_source="native_tool_calls",
        )

        plan = payload["plan"]
        metadata = payload["metadata"]
        assert plan.metadata == {"provider": "unit", "model": "model-x"}
        assert plan.batches[0].calls[0].name == "math.add"
        assert plan.batches[0].calls[0].arguments == {"a": 1, "b": 2}
        assert metadata["raw_tool_call_count"] == 1
        assert metadata["derived_batch_modes"] == ["sequential"]
        assert metadata["assistant_tool_message"]["content"] == "using a tool"
