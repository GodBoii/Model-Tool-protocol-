from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .protocol import ExecutionPlan

CURRENT_MTP_VERSION = "0.1.0"


@dataclass(slots=True)
class MessageEnvelope:
    mtp_version: str
    kind: str
    payload: dict[str, Any]
    metadata: dict[str, Any]

    @classmethod
    def create(
        cls,
        kind: str,
        payload: dict[str, Any],
        *,
        mtp_version: str = CURRENT_MTP_VERSION,
        metadata: dict[str, Any] | None = None,
    ) -> "MessageEnvelope":
        return cls(
            mtp_version=mtp_version,
            kind=kind,
            payload=payload,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mtp_version": self.mtp_version,
            "kind": self.kind,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MessageEnvelope":
        return cls(
            mtp_version=str(data.get("mtp_version", CURRENT_MTP_VERSION)),
            kind=str(data["kind"]),
            payload=dict(data.get("payload", {})),
            metadata=dict(data.get("metadata", {})),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, raw: str) -> "MessageEnvelope":
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Envelope JSON must decode to an object.")
        return cls.from_dict(data)


class PlanValidationError(ValueError):
    pass


class ToolArgumentsValidationError(ValueError):
    pass


def _validate_schema_type(expected: str, value: Any) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        errors: list[str] = []
        for option in any_of:
            if not isinstance(option, dict):
                continue
            try:
                _validate_value(value, option, path)
                return
            except ToolArgumentsValidationError as exc:
                errors.append(str(exc))
        joined = "; ".join(errors) if errors else "no anyOf branch matched"
        raise ToolArgumentsValidationError(f"{path}: {joined}")

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _validate_schema_type(expected_type, value):
        actual = type(value).__name__
        raise ToolArgumentsValidationError(
            f"{path}: expected {expected_type}, got {actual}"
        )

    if expected_type == "object" and isinstance(value, dict):
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}

        required = schema.get("required")
        if isinstance(required, list):
            missing = [name for name in required if isinstance(name, str) and name not in value]
            if missing:
                raise ToolArgumentsValidationError(f"{path}: missing required fields: {missing}")

        additional = schema.get("additionalProperties", True)
        if additional is False:
            unknown = [key for key in value if key not in properties]
            if unknown:
                raise ToolArgumentsValidationError(f"{path}: unknown fields: {unknown}")

        for key, child_value in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                _validate_value(child_value, child_schema, f"{path}.{key}")
        return

    if expected_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(value):
                _validate_value(item, item_schema, f"{path}[{idx}]")


def validate_tool_arguments(arguments: dict[str, Any], input_schema: dict[str, Any] | None) -> None:
    if input_schema is None:
        return
    _validate_value(arguments, input_schema, "$")


def validate_execution_plan(plan: ExecutionPlan) -> None:
    call_ids: list[str] = []
    deps_map: dict[str, list[str]] = {}

    for batch in plan.batches:
        for call in batch.calls:
            if call.id in deps_map:
                raise PlanValidationError(f"Duplicate call id: {call.id}")
            call_ids.append(call.id)
            deps_map[call.id] = list(call.depends_on)

    call_id_set = set(call_ids)
    for call_id, deps in deps_map.items():
        missing = [dep for dep in deps if dep not in call_id_set]
        if missing:
            raise PlanValidationError(
                f"Call {call_id} depends on missing call ids: {missing}"
            )

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {call_id: WHITE for call_id in call_ids}

    def visit(node: str, trail: list[str]) -> None:
        color[node] = GRAY
        for dep in deps_map[node]:
            if color[dep] == WHITE:
                visit(dep, trail + [dep])
            elif color[dep] == GRAY:
                raise PlanValidationError(
                    f"Cyclic dependency detected: {' -> '.join(trail + [dep])}"
                )
        color[node] = BLACK

    for call_id in call_ids:
        if color[call_id] == WHITE:
            visit(call_id, [call_id])
