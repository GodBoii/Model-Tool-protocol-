from __future__ import annotations

import json
import pytest

from mtp.schema import (
    MessageEnvelope,
    PlanValidationError,
    ToolArgumentsValidationError,
    coerce_tool_arguments,
    validate_execution_plan,
    validate_tool_arguments,
    CURRENT_MTP_VERSION,
)
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall


class TestMessageEnvelope:
    def test_create(self):
        env = MessageEnvelope.create("test", {"key": "value"})
        assert env.kind == "test"
        assert env.payload == {"key": "value"}
        assert env.mtp_version == CURRENT_MTP_VERSION
        assert env.metadata == {}

    def test_create_with_metadata(self):
        env = MessageEnvelope.create("test", {}, metadata={"source": "test"})
        assert env.metadata == {"source": "test"}

    def test_to_dict(self):
        env = MessageEnvelope.create("test", {"k": "v"})
        d = env.to_dict()
        assert d["mtp_version"] == CURRENT_MTP_VERSION
        assert d["kind"] == "test"
        assert d["payload"] == {"k": "v"}

    def test_from_dict(self):
        d = {"mtp_version": "0.1.0", "kind": "test", "payload": {"a": 1}, "metadata": {"b": 2}}
        env = MessageEnvelope.from_dict(d)
        assert env.kind == "test"
        assert env.payload == {"a": 1}
        assert env.metadata == {"b": 2}

    def test_from_dict_missing_kind_raises(self):
        with pytest.raises(KeyError):
            MessageEnvelope.from_dict({"payload": {}})

    def test_from_dict_defaults(self):
        env = MessageEnvelope.from_dict({"kind": "test"})
        assert env.mtp_version == CURRENT_MTP_VERSION
        assert env.payload == {}

    def test_json_roundtrip(self):
        env = MessageEnvelope.create("test", {"data": [1, 2, 3]}, metadata={"m": True})
        json_str = env.to_json()
        restored = MessageEnvelope.from_json(json_str)
        assert restored.kind == env.kind
        assert restored.payload == env.payload
        assert restored.metadata == env.metadata

    def test_from_json_non_dict_raises(self):
        with pytest.raises(ValueError, match="object"):
            MessageEnvelope.from_json('"just a string"')

    def test_from_json_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            MessageEnvelope.from_json("{invalid json")


class TestValidateToolArguments:
    def test_valid_object(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            "required": ["name"],
        }
        validate_tool_arguments({"name": "Alice", "age": 30}, schema)

    def test_missing_required(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        with pytest.raises(ToolArgumentsValidationError, match="missing required"):
            validate_tool_arguments({}, schema)

    def test_wrong_type(self):
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        with pytest.raises(ToolArgumentsValidationError, match="expected integer"):
            validate_tool_arguments({"x": "not an int"}, schema)

    def test_additional_properties_rejected(self):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "additionalProperties": False,
        }
        with pytest.raises(ToolArgumentsValidationError, match="unknown fields"):
            validate_tool_arguments({"a": "ok", "b": "extra"}, schema)

    def test_null_schema_passes(self):
        validate_tool_arguments({"anything": 123}, None)

    def test_enum_validation(self):
        schema = {"type": "object", "properties": {"color": {"enum": ["red", "green", "blue"]}}}
        validate_tool_arguments({"color": "red"}, schema)
        with pytest.raises(ToolArgumentsValidationError, match="expected one of"):
            validate_tool_arguments({"color": "yellow"}, schema)

    def test_const_validation(self):
        schema = {"type": "object", "properties": {"version": {"const": 1}}}
        validate_tool_arguments({"version": 1}, schema)
        with pytest.raises(ToolArgumentsValidationError, match="expected const"):
            validate_tool_arguments({"version": 2}, schema)

    def test_any_of(self):
        schema = {
            "type": "object",
            "properties": {
                "val": {"anyOf": [{"type": "string"}, {"type": "integer"}]}
            },
        }
        validate_tool_arguments({"val": "hello"}, schema)
        validate_tool_arguments({"val": 42}, schema)
        with pytest.raises(ToolArgumentsValidationError):
            validate_tool_arguments({"val": True}, schema)

    def test_one_of(self):
        schema = {
            "type": "object",
            "properties": {
                "val": {"oneOf": [{"type": "string"}, {"type": "integer"}]}
            },
        }
        validate_tool_arguments({"val": "hello"}, schema)
        validate_tool_arguments({"val": 42}, schema)

    def test_all_of(self):
        schema = {
            "type": "object",
            "properties": {
                "val": {
                    "allOf": [
                        {"type": "integer"},
                        {"minimum": 0},
                    ]
                }
            },
        }
        validate_tool_arguments({"val": 5}, schema)
        with pytest.raises(ToolArgumentsValidationError):
            validate_tool_arguments({"val": -1}, schema)

    def test_not_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "val": {"not": {"type": "string"}}
            },
        }
        validate_tool_arguments({"val": 42}, schema)
        with pytest.raises(ToolArgumentsValidationError, match="disallowed"):
            validate_tool_arguments({"val": "hello"}, schema)

    def test_minimum_maximum(self):
        schema = {"type": "object", "properties": {"x": {"type": "number", "minimum": 0, "maximum": 100}}}
        validate_tool_arguments({"x": 50}, schema)
        with pytest.raises(ToolArgumentsValidationError, match=">="):
            validate_tool_arguments({"x": -1}, schema)
        with pytest.raises(ToolArgumentsValidationError, match="<="):
            validate_tool_arguments({"x": 101}, schema)

    def test_min_max_length(self):
        schema = {"type": "object", "properties": {"s": {"type": "string", "minLength": 2, "maxLength": 5}}}
        validate_tool_arguments({"s": "abc"}, schema)
        with pytest.raises(ToolArgumentsValidationError, match="length >="):
            validate_tool_arguments({"s": "a"}, schema)
        with pytest.raises(ToolArgumentsValidationError, match="length <="):
            validate_tool_arguments({"s": "abcdef"}, schema)

    def test_pattern(self):
        schema = {"type": "object", "properties": {"email": {"type": "string", "pattern": r"^.+@.+$"}}}
        validate_tool_arguments({"email": "a@b"}, schema)
        with pytest.raises(ToolArgumentsValidationError, match="pattern"):
            validate_tool_arguments({"email": "no-at"}, schema)

    def test_array_min_max_items(self):
        schema = {"type": "object", "properties": {"items": {"type": "array", "minItems": 1, "maxItems": 3}}}
        validate_tool_arguments({"items": [1]}, schema)
        with pytest.raises(ToolArgumentsValidationError, match="at least"):
            validate_tool_arguments({"items": []}, schema)
        with pytest.raises(ToolArgumentsValidationError, match="at most"):
            validate_tool_arguments({"items": [1, 2, 3, 4]}, schema)

    def test_array_items_validation(self):
        schema = {
            "type": "object",
            "properties": {"nums": {"type": "array", "items": {"type": "integer"}}},
        }
        validate_tool_arguments({"nums": [1, 2, 3]}, schema)
        with pytest.raises(ToolArgumentsValidationError):
            validate_tool_arguments({"nums": [1, "two"]}, schema)

    def test_type_list(self):
        schema = {"type": "object", "properties": {"v": {"type": ["string", "integer"]}}}
        validate_tool_arguments({"v": "hello"}, schema)
        validate_tool_arguments({"v": 42}, schema)
        with pytest.raises(ToolArgumentsValidationError):
            validate_tool_arguments({"v": True}, schema)

    def test_schema_ref(self):
        schema = {
            "type": "object",
            "properties": {"name": {"$ref": "#/definitions/Name"}},
            "definitions": {"Name": {"type": "string"}},
        }
        validate_tool_arguments({"name": "Alice"}, schema)
        with pytest.raises(ToolArgumentsValidationError):
            validate_tool_arguments({"name": 123}, schema)

    def test_unresolved_ref_raises(self):
        schema = {
            "type": "object",
            "properties": {"name": {"$ref": "#/definitions/NonExistent"}},
        }
        with pytest.raises(ToolArgumentsValidationError, match="unresolved"):
            validate_tool_arguments({"name": "x"}, schema)


class TestCoerceToolArguments:
    def test_coerces_number_like_strings(self):
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "integer"},
                "ok": {"type": "boolean"},
            },
            "required": ["a", "b", "ok"],
            "additionalProperties": False,
        }
        coerced = coerce_tool_arguments({"a": "18", "b": "6", "ok": "true"}, schema)
        assert coerced == {"a": 18, "b": 6, "ok": True}
        validate_tool_arguments(coerced, schema)

    def test_coerces_nested_anyof_payloads(self):
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "value": {"anyOf": [{"type": "number"}, {"type": "string"}]},
                        },
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        }
        coerced = coerce_tool_arguments({"items": [{"value": "4.5"}, {"value": "hello"}]}, schema)
        assert coerced == {"items": [{"value": 4.5}, {"value": "hello"}]}
        validate_tool_arguments(coerced, schema)

    def test_preserves_ref_objects(self):
        schema = {
            "type": "object",
            "properties": {
                "a": {
                    "anyOf": [
                        {"type": "number"},
                        {
                            "type": "object",
                            "properties": {"$ref": {"type": "string"}},
                            "required": ["$ref"],
                            "additionalProperties": False,
                        },
                    ]
                }
            },
            "required": ["a"],
            "additionalProperties": False,
        }
        coerced = coerce_tool_arguments({"a": {"$ref": "call_1"}}, schema)
        assert coerced == {"a": {"$ref": "call_1"}}
        validate_tool_arguments(coerced, schema)

    def test_parses_stringified_nested_ref_object(self):
        schema = {
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
        }
        coerced = coerce_tool_arguments({"a": "4", "b": "{\"$ref\":\"call_1\"}"}, schema)
        assert coerced == {"a": 4, "b": {"$ref": "call_1"}}
        validate_tool_arguments(coerced, schema)


class TestValidateExecutionPlan:
    def test_valid_plan(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(mode="sequential", calls=[ToolCall(id="c1", name="a")]),
                ToolBatch(mode="sequential", calls=[ToolCall(id="c2", name="b", depends_on=["c1"])]),
            ]
        )
        validate_execution_plan(plan)

    def test_duplicate_id_raises(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(id="c1", name="a"),
                        ToolCall(id="c1", name="b"),
                    ],
                )
            ]
        )
        with pytest.raises(PlanValidationError, match="Duplicate"):
            validate_execution_plan(plan)

    def test_missing_dependency_raises(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[ToolCall(id="c2", name="b", depends_on=["c1"])],
                )
            ]
        )
        with pytest.raises(PlanValidationError, match="missing call ids"):
            validate_execution_plan(plan)

    def test_cycle_raises(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[
                        ToolCall(id="c1", name="a", depends_on=["c2"]),
                        ToolCall(id="c2", name="b", depends_on=["c1"]),
                    ],
                )
            ]
        )
        with pytest.raises(PlanValidationError, match="[Cc]yclic"):
            validate_execution_plan(plan)

    def test_valid_ref_in_sequential(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(mode="sequential", calls=[ToolCall(id="c1", name="a")]),
                ToolBatch(
                    mode="sequential",
                    calls=[ToolCall(id="c2", name="b", arguments={"x": {"$ref": "c1"}})],
                ),
            ]
        )
        validate_execution_plan(plan)

    def test_ref_to_later_batch_raises(self):
        plan = ExecutionPlan(
            batches=[
                ToolBatch(
                    mode="sequential",
                    calls=[ToolCall(id="c1", name="a", arguments={"x": {"$ref": "c2"}})],
                ),
                ToolBatch(mode="sequential", calls=[ToolCall(id="c2", name="b")]),
            ]
        )
        with pytest.raises(PlanValidationError, match="unavailable"):
            validate_execution_plan(plan)

    def test_empty_plan_valid(self):
        validate_execution_plan(ExecutionPlan())
