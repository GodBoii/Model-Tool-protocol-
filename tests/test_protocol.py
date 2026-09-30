from __future__ import annotations

import pytest
from datetime import UTC, datetime

from mtp.protocol import (
    ExecutionPlan,
    ToolBatch,
    ToolCall,
    ToolOutput,
    ToolResult,
    ToolRiskLevel,
    ToolSpec,
)
from mtp.media import Image, Audio, Video, File


class TestToolSpec:
    def test_defaults(self):
        spec = ToolSpec(name="x", description="d")
        assert spec.name == "x"
        assert spec.description == "d"
        assert spec.input_schema == {}
        assert spec.tags == []
        assert spec.risk_level == ToolRiskLevel.READ_ONLY
        assert spec.cost_hint == "unknown"
        assert spec.side_effects == "none"
        assert spec.cache_ttl_seconds == 0

    def test_custom_values(self):
        spec = ToolSpec(
            name="fs.write",
            description="Write file",
            input_schema={"type": "object"},
            tags=["io", "write"],
            risk_level=ToolRiskLevel.WRITE,
            cost_hint="medium",
            side_effects="modifies filesystem",
            cache_ttl_seconds=60,
        )
        assert spec.risk_level == ToolRiskLevel.WRITE
        assert spec.tags == ["io", "write"]
        assert spec.cache_ttl_seconds == 60

    def test_risk_level_enum_values(self):
        assert ToolRiskLevel.READ_ONLY.value == "read_only"
        assert ToolRiskLevel.WRITE.value == "write"
        assert ToolRiskLevel.DESTRUCTIVE.value == "destructive"

    def test_risk_level_is_str(self):
        assert isinstance(ToolRiskLevel.READ_ONLY, str)
        assert ToolRiskLevel.READ_ONLY == "read_only"


class TestToolCall:
    def test_defaults(self):
        call = ToolCall(id="c1", name="test")
        assert call.id == "c1"
        assert call.name == "test"
        assert call.arguments == {}
        assert call.depends_on == []
        assert call.reasoning is None

    def test_with_arguments_and_deps(self):
        call = ToolCall(
            id="c2",
            name="math.add",
            arguments={"a": 1, "b": 2},
            depends_on=["c1"],
            reasoning="Adding numbers",
        )
        assert call.arguments == {"a": 1, "b": 2}
        assert call.depends_on == ["c1"]
        assert call.reasoning == "Adding numbers"


class TestToolResult:
    def test_defaults(self):
        r = ToolResult(call_id="c1", tool_name="test", output="ok")
        assert r.success is True
        assert r.error is None
        assert r.cached is False
        assert r.approval is None
        assert r.skipped is False
        assert isinstance(r.created_at, datetime)
        assert r.expires_at is None
        assert r.images is None
        assert r.videos is None
        assert r.audios is None
        assert r.files is None

    def test_failure_result(self):
        r = ToolResult(call_id="c1", tool_name="test", output=None, success=False, error="boom")
        assert r.success is False
        assert r.error == "boom"
        assert r.output is None

    def test_multimedia_result(self):
        imgs = [Image(url="http://x/img.png")]
        r = ToolResult(call_id="c1", tool_name="test", output="ok", images=imgs)
        assert len(r.images) == 1
        assert r.images[0].url == "http://x/img.png"


class TestToolOutput:
    def test_basic(self):
        out = ToolOutput(content="hello")
        assert out.content == "hello"
        assert out.images is None

    def test_with_media(self):
        imgs = [Image(url="http://x/img.png")]
        out = ToolOutput(content="result", images=imgs)
        assert len(out.images) == 1


class TestToolBatch:
    def test_parallel(self):
        batch = ToolBatch(mode="parallel", calls=[ToolCall(id="c1", name="a")])
        assert batch.mode == "parallel"

    def test_sequential(self):
        batch = ToolBatch(mode="sequential", calls=[ToolCall(id="c1", name="a")])
        assert batch.mode == "sequential"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="parallel.*sequential"):
            ToolBatch(mode="invalid", calls=[])

    def test_empty_calls(self):
        batch = ToolBatch(mode="parallel", calls=[])
        assert batch.calls == []


class TestExecutionPlan:
    def test_empty(self):
        plan = ExecutionPlan()
        assert plan.batches == []
        assert plan.metadata == {}

    def test_with_metadata(self):
        plan = ExecutionPlan(metadata={"planner": "test"})
        assert plan.metadata["planner"] == "test"

    def test_single_batch(self):
        batch = ToolBatch(mode="sequential", calls=[ToolCall(id="c1", name="a")])
        plan = ExecutionPlan(batches=[batch])
        assert len(plan.batches) == 1
        assert plan.batches[0].calls[0].id == "c1"
