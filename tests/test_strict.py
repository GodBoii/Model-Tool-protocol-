from __future__ import annotations

import pytest

from mtp.strict import StrictViolation, _has_ref, _collect_refs, validate_strict_dependencies
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall


class TestHasRef:
    def test_simple_ref(self):
        assert _has_ref({"$ref": "c1"}) is True

    def test_no_ref(self):
        assert _has_ref({"key": "value"}) is False

    def test_nested_ref(self):
        assert _has_ref({"outer": {"inner": {"$ref": "c1"}}}) is True

    def test_list_ref(self):
        assert _has_ref([{"$ref": "c1"}]) is True

    def test_non_dict(self):
        assert _has_ref("string") is False
        assert _has_ref(42) is False
        assert _has_ref(None) is False

    def test_ref_non_string_ignored(self):
        assert _has_ref({"$ref": 123}) is False


class TestCollectRefs:
    def test_simple(self):
        assert _collect_refs({"$ref": "c1"}) == ["c1"]

    def test_multiple(self):
        val = {"a": {"$ref": "c1"}, "b": {"$ref": "c2"}}
        refs = _collect_refs(val)
        assert "c1" in refs
        assert "c2" in refs

    def test_nested_list(self):
        val = {"items": [{"$ref": "c1"}, {"$ref": "c2"}]}
        refs = _collect_refs(val)
        assert refs == ["c1", "c2"]

    def test_no_refs(self):
        assert _collect_refs({"a": 1, "b": "x"}) == []

    def test_deeply_nested(self):
        val = {"l1": {"l2": {"l3": {"$ref": "deep"}}}}
        assert _collect_refs(val) == ["deep"]

    def test_empty(self):
        assert _collect_refs({}) == []


class TestValidateStrictDependencies:
    def test_no_refs_no_violations(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(id="c1", name="a", arguments={"x": 1}),
                        ToolCall(id="c2", name="b", arguments={"y": 2}),
                    ],
                )
            ]
        )
        violations = validate_strict_dependencies(plan)
        assert violations == []

    def test_ref_with_correct_depends_on(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(id="c1", name="a"),
                        ToolCall(
                            id="c2",
                            name="b",
                            arguments={"x": {"$ref": "c1"}},
                            depends_on=["c1"],
                        ),
                    ],
                )
            ]
        )
        violations = validate_strict_dependencies(plan)
        assert violations == []

    def test_ref_without_depends_on_violation(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(id="c1", name="a"),
                        ToolCall(
                            id="c2",
                            name="b",
                            arguments={"x": {"$ref": "c1"}},
                            depends_on=[],
                        ),
                    ],
                )
            ]
        )
        violations = validate_strict_dependencies(plan)
        assert len(violations) == 1
        assert "c1" in violations[0].message
        assert violations[0].call_id == "c2"

    def test_partial_depends_on_violation(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(id="c1", name="a"),
                        ToolCall(id="c2", name="b"),
                        ToolCall(
                            id="c3",
                            name="c",
                            arguments={"a": {"$ref": "c1"}, "b": {"$ref": "c2"}},
                            depends_on=["c1"],
                        ),
                    ],
                )
            ]
        )
        violations = validate_strict_dependencies(plan)
        assert len(violations) == 1
        assert "c2" in violations[0].message

    def test_multiple_batches(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(mode="sequential", calls=[ToolCall(id="c1", name="a")]),
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(
                            id="c2",
                            name="b",
                            arguments={"x": {"$ref": "c1"}},
                            depends_on=["c1"],
                        )
                    ],
                ),
            ]
        )
        violations = validate_strict_dependencies(plan)
        assert violations == []

    def test_nested_ref_detected(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(id="c1", name="a"),
                        ToolCall(
                            id="c2",
                            name="b",
                            arguments={"data": {"nested": {"$ref": "c1"}}},
                            depends_on=[],
                        ),
                    ],
                )
            ]
        )
        violations = validate_strict_dependencies(plan)
        assert len(violations) == 1

    def test_empty_plan(self):
        violations = validate_strict_dependencies(ExecutionPlan())
        assert violations == []

    def test_violation_fields(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(id="c1", name="a"),
                        ToolCall(
                            id="c2",
                            name="b.tool",
                            arguments={"x": {"$ref": "c1"}},
                        ),
                    ],
                )
            ]
        )
        violations = validate_strict_dependencies(plan)
        assert len(violations) == 1
        v = violations[0]
        assert isinstance(v, StrictViolation)
        assert v.call_id == "c2"
        assert v.tool_name == "b.tool"
