from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall
from mtp.strict import validate_strict_dependencies


def test_strict_dependencies_allow_independent_same_namespace_parallel_calls() -> None:
    plan = ExecutionPlan(
        batches=[
            ToolBatch(
                mode="parallel",
                calls=[
                    ToolCall(id="call_1", name="fs.search", arguments={"query": "spinner"}),
                    ToolCall(id="call_2", name="fs.search", arguments={"query": "status bar"}),
                ],
            )
        ]
    )

    assert validate_strict_dependencies(plan) == []


def test_strict_dependencies_require_ref_to_be_listed_in_depends_on() -> None:
    plan = ExecutionPlan(
        batches=[
            ToolBatch(
                mode="parallel",
                calls=[
                    ToolCall(id="call_1", name="fs.read_text", arguments={"path": "README.md"}),
                    ToolCall(
                        id="call_2",
                        name="fs.write_text",
                        arguments={"path": "out.txt", "content": {"$ref": "call_1"}},
                        depends_on=[],
                    ),
                ],
            )
        ]
    )

    violations = validate_strict_dependencies(plan)

    assert len(violations) == 1
    assert violations[0].call_id == "call_2"
    assert "depends_on" in violations[0].message
