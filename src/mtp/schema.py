from __future__ import annotations

from dataclasses import dataclass
import json
import re
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


def _coerce_scalar(expected: str, value: Any) -> Any:
    if expected == "integer":
        if isinstance(value, bool):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if re.fullmatch(r"[+-]?\d+", stripped):
                try:
                    return int(stripped)
                except Exception:
                    return value
        return value
    if expected == "number":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", stripped):
                try:
                    number = float(stripped)
                except Exception:
                    return value
                return int(number) if number.is_integer() else number
        return value
    if expected == "boolean" and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return value
    if expected == "null" and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"null", "none"}:
            return None
        return value
    return value


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


def _resolve_schema_ref(ref: str, root_schema: dict[str, Any]) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    current: Any = root_schema
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, dict) else None


def _validate_combinator(
    value: Any,
    branches: Any,
    path: str,
    root_schema: dict[str, Any],
    *,
    keyword: str,
) -> None:
    if not isinstance(branches, list) or not branches:
        return
    matches = 0
    errors: list[str] = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        try:
            _validate_value(value, branch, path, root_schema)
            matches += 1
        except ToolArgumentsValidationError as exc:
            errors.append(str(exc))
    if keyword == "anyOf" and matches < 1:
        joined = "; ".join(errors) if errors else "no anyOf branch matched"
        raise ToolArgumentsValidationError(f"{path}: {joined}")
    if keyword == "oneOf" and matches != 1:
        raise ToolArgumentsValidationError(f"{path}: expected exactly one oneOf match, got {matches}")


def _validate_value(value: Any, schema: dict[str, Any], path: str, root_schema: dict[str, Any]) -> None:
    ref = schema.get("$ref")
    if isinstance(ref, str):
        resolved = _resolve_schema_ref(ref, root_schema)
        if resolved is None:
            raise ToolArgumentsValidationError(f"{path}: unresolved schema ref {ref}")
        _validate_value(value, resolved, path, root_schema)
        return

    const = schema.get("const")
    if "const" in schema and value != const:
        raise ToolArgumentsValidationError(f"{path}: expected const {const!r}")

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise ToolArgumentsValidationError(f"{path}: expected one of {enum!r}")

    _validate_combinator(value, schema.get("anyOf"), path, root_schema, keyword="anyOf")
    _validate_combinator(value, schema.get("oneOf"), path, root_schema, keyword="oneOf")

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for branch in all_of:
            if isinstance(branch, dict):
                _validate_value(value, branch, path, root_schema)

    not_schema = schema.get("not")
    if isinstance(not_schema, dict):
        try:
            _validate_value(value, not_schema, path, root_schema)
        except ToolArgumentsValidationError:
            pass
        else:
            raise ToolArgumentsValidationError(f"{path}: value matches disallowed schema")

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        errors: list[str] = []
        for option in any_of:
            if not isinstance(option, dict):
                continue
            try:
                _validate_value(value, option, path, root_schema)
                return
            except ToolArgumentsValidationError as exc:
                errors.append(str(exc))
        joined = "; ".join(errors) if errors else "no anyOf branch matched"
        raise ToolArgumentsValidationError(f"{path}: {joined}")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(isinstance(item, str) and _validate_schema_type(item, value) for item in expected_type):
            actual = type(value).__name__
            raise ToolArgumentsValidationError(f"{path}: expected one of {expected_type}, got {actual}")
    elif isinstance(expected_type, str) and not _validate_schema_type(expected_type, value):
        actual = type(value).__name__
        raise ToolArgumentsValidationError(
            f"{path}: expected {expected_type}, got {actual}"
        )

    if (expected_type == "object" or (isinstance(expected_type, list) and "object" in expected_type)) and isinstance(value, dict):
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
                _validate_value(child_value, child_schema, f"{path}.{key}", root_schema)
        return

    if (expected_type == "array" or (isinstance(expected_type, list) and "array" in expected_type)) and isinstance(value, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            raise ToolArgumentsValidationError(f"{path}: expected at least {min_items} items")
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            raise ToolArgumentsValidationError(f"{path}: expected at most {max_items} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(value):
                _validate_value(item, item_schema, f"{path}[{idx}]", root_schema)
        return

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise ToolArgumentsValidationError(f"{path}: expected >= {minimum}")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise ToolArgumentsValidationError(f"{path}: expected <= {maximum}")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise ToolArgumentsValidationError(f"{path}: expected length >= {min_length}")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            raise ToolArgumentsValidationError(f"{path}: expected length <= {max_length}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise ToolArgumentsValidationError(f"{path}: does not match pattern {pattern!r}")


def _coerce_value(value: Any, schema: dict[str, Any], root_schema: dict[str, Any]) -> Any:
    ref = schema.get("$ref")
    if isinstance(ref, str):
        resolved = _resolve_schema_ref(ref, root_schema)
        if resolved is not None:
            return _coerce_value(value, resolved, root_schema)
        return value

    combinators = []
    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        combinators.append(any_of)
    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        combinators.append(one_of)
    for branches in combinators:
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            candidate = _coerce_value(value, branch, root_schema)
            try:
                _validate_value(candidate, branch, "$", root_schema)
                return candidate
            except ToolArgumentsValidationError:
                continue

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        for item in expected_type:
            if not isinstance(item, str):
                continue
            candidate = _coerce_value(value, {"type": item}, root_schema)
            if _validate_schema_type(item, candidate):
                return candidate
        return value

    if expected_type == "object":
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = json.loads(stripped)
                except Exception:
                    parsed = value
                else:
                    value = parsed
        if isinstance(value, dict):
            properties = schema.get("properties")
            if not isinstance(properties, dict):
                return value
            coerced: dict[str, Any] = {}
            for key, item in value.items():
                child_schema = properties.get(key)
                if isinstance(child_schema, dict):
                    coerced[key] = _coerce_value(item, child_schema, root_schema)
                else:
                    coerced[key] = item
            return coerced
        return value

    if expected_type == "array":
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                try:
                    parsed = json.loads(stripped)
                except Exception:
                    parsed = value
                else:
                    value = parsed
        if isinstance(value, list):
            item_schema = schema.get("items")
            if not isinstance(item_schema, dict):
                return value
            return [_coerce_value(item, item_schema, root_schema) for item in value]
        return value

    if isinstance(expected_type, str):
        return _coerce_scalar(expected_type, value)
    return value


def coerce_tool_arguments(arguments: dict[str, Any], input_schema: dict[str, Any] | None) -> dict[str, Any]:
    if input_schema is None:
        return arguments
    coerced = _coerce_value(arguments, input_schema, input_schema)
    return coerced if isinstance(coerced, dict) else arguments


def validate_tool_arguments(arguments: dict[str, Any], input_schema: dict[str, Any] | None) -> None:
    if input_schema is None:
        return
    _validate_value(arguments, input_schema, "$", input_schema)


def validate_execution_plan(plan: ExecutionPlan, *, prior_call_ids: set[str] | None = None) -> None:
    prior_ids = set(prior_call_ids or ())
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
        missing = [dep for dep in deps if dep not in call_id_set and dep not in prior_ids]
        if missing:
            raise PlanValidationError(
                f"Call {call_id} depends on missing call ids: {missing}"
            )

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {call_id: WHITE for call_id in call_ids}

    def visit(node: str, trail: list[str]) -> None:
        color[node] = GRAY
        for dep in deps_map[node]:
            if dep not in color:
                continue
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

    completed: set[str] = prior_ids - call_id_set
    for batch in plan.batches:
        available = set(completed)
        if batch.mode == "sequential":
            for call in batch.calls:
                refs = _collect_refs(call.arguments)
                unavailable = [ref for ref in refs if ref not in available]
                if unavailable:
                    raise PlanValidationError(
                        f"Call {call.id} references unavailable prior call ids: {unavailable}"
                    )
                deps = [dep for dep in call.depends_on if dep not in available]
                if deps:
                    raise PlanValidationError(
                        f"Call {call.id} depends on unavailable prior call ids: {deps}"
                    )
                available.add(call.id)
        else:
            batch_ids = {call.id for call in batch.calls}
            for call in batch.calls:
                refs = _collect_refs(call.arguments)
                unavailable = [ref for ref in refs if ref not in completed]
                if unavailable:
                    raise PlanValidationError(
                        f"Parallel call {call.id} references unavailable prior call ids: {unavailable}"
                    )
                deps = [dep for dep in call.depends_on if dep not in completed]
                if deps:
                    raise PlanValidationError(
                        f"Parallel call {call.id} depends on same/later batch call ids: {deps}"
                    )
            available.update(batch_ids)
        completed = available
