from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ExecutionPlan, ToolCall


@dataclass(slots=True)
class StrictViolation:
    message: str
    call_id: str
    tool_name: str


def _has_ref(value: Any) -> bool:
    if isinstance(value, dict):
        if "$ref" in value and isinstance(value["$ref"], str):
            return True
        return any(_has_ref(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_ref(v) for v in value)
    return False


def _collect_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        if "$ref" in value and isinstance(value["$ref"], str):
            refs.append(value["$ref"])
        for item in value.values():
            refs.extend(_collect_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_collect_refs(item))
    return refs


def _namespace(tool_name: str) -> str:
    return tool_name.split(".", 1)[0] if "." in tool_name else tool_name


def validate_strict_dependencies(plan: ExecutionPlan) -> list[StrictViolation]:
    """
    Enforces explicit dependency correctness for tool argument wiring.

    Rule:
    - Independent same-toolkit calls in the same batch are allowed.
    - If a call argument contains `$ref`, every referenced call id must also
      appear in `depends_on`.

    Existence checks for referenced ids and cycle detection are handled by
    execution-plan validation elsewhere in the runtime.
    """
    violations: list[StrictViolation] = []

    for batch in plan.batches:
        for call in batch.calls:
            refs = list(dict.fromkeys(_collect_refs(call.arguments)))
            if not refs:
                continue
            missing_dep_refs = [ref for ref in refs if ref not in call.depends_on]
            if missing_dep_refs:
                violations.append(
                    StrictViolation(
                        message=(
                            "Strict dependency mode: each $ref must also appear in depends_on. "
                            f"Missing refs in depends_on: {missing_dep_refs}"
                        ),
                        call_id=call.id,
                        tool_name=call.name,
                    )
                )

    return violations
