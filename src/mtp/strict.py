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
    Enforces explicit dependencies for multi-call same-namespace batches.

    Rule:
    - In the same batch, if multiple calls target the same toolkit namespace
      (e.g. calculator.*), all calls after the first must declare dependency
      using `depends_on` or include at least one `$ref` in arguments.
    """
    violations: list[StrictViolation] = []

    for batch in plan.batches:
        seen_by_namespace: dict[str, list[ToolCall]] = {}
        for call in batch.calls:
            seen_by_namespace.setdefault(_namespace(call.name), []).append(call)

        for ns_calls in seen_by_namespace.values():
            if len(ns_calls) <= 1:
                continue
            for call in ns_calls[1:]:
                has_dep = bool(call.depends_on)
                has_ref = _has_ref(call.arguments)
                if not has_dep and not has_ref:
                    violations.append(
                        StrictViolation(
                            message=(
                                "Strict dependency mode: multi-call same-toolkit batch "
                                "requires explicit depends_on or $ref argument wiring."
                            ),
                            call_id=call.id,
                            tool_name=call.name,
                        )
                    )
                refs = list(dict.fromkeys(_collect_refs(call.arguments)))
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
