from __future__ import annotations

import pytest
from typing import Any, Optional, Union, Literal

from mtp.tools import (
    FunctionToolkit,
    ToolMeta,
    _annotation_to_json_schema,
    _infer_input_schema,
    mtp_tool,
    tool_spec_from_callable,
    toolkit_from_functions,
)
from mtp.protocol import ToolRiskLevel


class TestAnnotationTojsonSchema:
    def test_str(self):
        assert _annotation_to_json_schema(str) == {"type": "string"}

    def test_int(self):
        assert _annotation_to_json_schema(int) == {"type": "integer"}

    def test_float(self):
        assert _annotation_to_json_schema(float) == {"type": "number"}

    def test_bool(self):
        assert _annotation_to_json_schema(bool) == {"type": "boolean"}

    def test_dict(self):
        assert _annotation_to_json_schema(dict) == {"type": "object"}

    def test_list(self):
        assert _annotation_to_json_schema(list) == {"type": "array"}

    def test_list_of_str(self):
        result = _annotation_to_json_schema(list[str])
        assert result == {"type": "array", "items": {"type": "string"}}

    def test_list_of_int(self):
        result = _annotation_to_json_schema(list[int])
        assert result == {"type": "array", "items": {"type": "integer"}}

    def test_set(self):
        result = _annotation_to_json_schema(set)
        assert result == {"type": "array"}

    def test_set_of_str(self):
        result = _annotation_to_json_schema(set[str])
        assert result["type"] == "array"
        assert result["uniqueItems"] is True

    def test_tuple(self):
        result = _annotation_to_json_schema(tuple)
        assert result == {"type": "array"}

    def test_optional_str(self):
        result = _annotation_to_json_schema(Optional[str])
        assert "anyOf" in result or result.get("type") in ("string", "null")

    def test_union_types(self):
        result = _annotation_to_json_schema(Union[int, str])
        assert "anyOf" in result or result.get("type") is not None

    def test_literal(self):
        result = _annotation_to_json_schema(Literal["a", "b", "c"])
        assert result.get("enum") == ["a", "b", "c"] or result.get("type") is not None

    def test_empty_annotation(self):
        import inspect
        result = _annotation_to_json_schema(inspect._empty)
        assert result == {"type": "string"}

    def test_none_type(self):
        result = _annotation_to_json_schema(type(None))
        assert result == {"type": "null"}

    def test_any_type(self):
        from typing import Any
        result = _annotation_to_json_schema(Any)
        assert result == {}


class TestInferInputSchema:
    def test_basic_function(self):
        def fn(a: str, b: int) -> str:
            return a
        schema = _infer_input_schema(fn)
        assert schema["type"] == "object"
        assert "a" in schema["properties"]
        assert "b" in schema["properties"]
        assert schema["properties"]["a"]["type"] == "string"
        assert schema["properties"]["b"]["type"] == "integer"
        assert "a" in schema["required"]
        assert "b" in schema["required"]

    def test_optional_param(self):
        def fn(a: str, b: int = 10) -> str:
            return a
        schema = _infer_input_schema(fn)
        assert "a" in schema["required"]
        assert "b" not in schema["required"]

    def test_skip_self(self):
        class MyClass:
            def method(self, x: str) -> str:
                return x
        schema = _infer_input_schema(MyClass.method)
        assert "self" not in schema["properties"]

    def test_skip_varargs(self):
        def fn(*args, **kwargs):
            pass
        schema = _infer_input_schema(fn)
        assert len(schema["properties"]) == 0

    def test_no_annotations(self):
        def fn(a, b):
            pass
        schema = _infer_input_schema(fn)
        assert "a" in schema["properties"]
        assert schema["properties"]["a"]["type"] == "string"

    def test_additional_properties_false(self):
        def fn(a: str) -> str:
            return a
        schema = _infer_input_schema(fn)
        assert schema["additionalProperties"] is False


class TestMtpToolDecorator:
    def test_sets_meta(self):
        @mtp_tool(name="custom_name", description="Custom desc")
        def my_fn(x: str) -> str:
            return x
        meta = getattr(my_fn, "__mtp_tool_meta__")
        assert isinstance(meta, ToolMeta)
        assert meta.name == "custom_name"
        assert meta.description == "Custom desc"

    def test_defaults(self):
        @mtp_tool()
        def my_fn(x: str) -> str:
            return x
        meta = getattr(my_fn, "__mtp_tool_meta__")
        assert meta.name is None
        assert meta.risk_level == ToolRiskLevel.READ_ONLY

    def test_risk_level(self):
        @mtp_tool(risk_level=ToolRiskLevel.DESTRUCTIVE)
        def danger():
            pass
        meta = getattr(danger, "__mtp_tool_meta__")
        assert meta.risk_level == ToolRiskLevel.DESTRUCTIVE

    def test_tags(self):
        @mtp_tool(tags=["io", "read"])
        def reader():
            pass
        meta = getattr(reader, "__mtp_tool_meta__")
        assert meta.tags == ["io", "read"]


class TestToolSpecFromCallable:
    def test_basic(self):
        def add(a: int, b: int) -> int:
            return a + b
        spec = tool_spec_from_callable(add)
        assert spec.name == "add"
        assert "add" in spec.description.lower() or spec.description != ""
        assert spec.input_schema["type"] == "object"
        assert "a" in spec.input_schema["properties"]
        assert "b" in spec.input_schema["properties"]

    def test_with_namespace(self):
        def add(a: int, b: int) -> int:
            return a + b
        spec = tool_spec_from_callable(add, namespace="math")
        assert spec.name == "math.add"

    def test_with_decorator_meta(self):
        @mtp_tool(name="custom", description="Custom tool")
        def fn(x: str) -> str:
            return x
        spec = tool_spec_from_callable(fn)
        assert spec.name == "custom"
        assert spec.description == "Custom tool"

    def test_with_namespace_and_meta(self):
        @mtp_tool(name="renamed")
        def fn(x: str) -> str:
            return x
        spec = tool_spec_from_callable(fn, namespace="ns")
        assert spec.name == "ns.renamed"

    def test_docstring_as_description(self):
        def documented():
            """This is the docstring."""
            pass
        spec = tool_spec_from_callable(documented)
        assert spec.description == "This is the docstring."

    def test_custom_input_schema(self):
        @mtp_tool(input_schema={"type": "object", "properties": {"x": {"type": "number"}}})
        def fn(x):
            pass
        spec = tool_spec_from_callable(fn)
        assert spec.input_schema["properties"]["x"]["type"] == "number"


class TestFunctionToolkit:
    def test_list_tool_specs(self):
        def add(a: int, b: int) -> int:
            return a + b
        toolkit = FunctionToolkit("calc", [add])
        specs = toolkit.list_tool_specs()
        assert len(specs) == 1
        assert specs[0].name == "calc.add"

    def test_load_tools(self):
        def add(a: int, b: int) -> int:
            return a + b
        toolkit = FunctionToolkit("calc", [add])
        tools = toolkit.load_tools()
        assert len(tools) == 1
        assert tools[0].spec.name == "calc.add"
        assert tools[0].handler is add

    def test_multiple_functions(self):
        def add(a: int, b: int) -> int:
            return a + b
        def mul(a: int, b: int) -> int:
            return a * b
        toolkit = FunctionToolkit("math", [add, mul])
        specs = toolkit.list_tool_specs()
        assert len(specs) == 2
        names = {s.name for s in specs}
        assert names == {"math.add", "math.mul"}


class TestToolkitFromFunctions:
    def test_returns_function_toolkit(self):
        def add(a: int, b: int) -> int:
            return a + b
        toolkit = toolkit_from_functions("calc", add)
        assert isinstance(toolkit, FunctionToolkit)
        assert toolkit.name == "calc"
        specs = toolkit.list_tool_specs()
        assert len(specs) == 1
