from __future__ import annotations

import asyncio
import os
import pytest
from pathlib import Path

from mtp.runtime import ToolRegistry
from mtp.protocol import ToolSpec
from mtp.toolkits.calculator import CalculatorToolkit
from mtp.toolkits.file_toolkit import FileToolkit
from mtp.toolkits.shell_toolkit import ShellToolkit
from mtp.toolkits.common import ref_schema, allow_ref
from mtp.toolkits.local import register_local_toolkits


class TestCommonHelpers:
    def test_ref_schema(self):
        schema = ref_schema()
        assert schema["type"] == "object"
        assert "$ref" in schema["properties"]
        assert "$ref" in schema["required"]
        assert schema["additionalProperties"] is False

    def test_allow_ref(self):
        base = {"type": "number"}
        result = allow_ref(base)
        assert "anyOf" in result
        assert len(result["anyOf"]) == 2
        assert base in result["anyOf"]
        assert ref_schema() in result["anyOf"]


class TestCalculatorToolkit:
    def test_list_specs(self):
        toolkit = CalculatorToolkit()
        specs = toolkit.list_tool_specs()
        names = {s.name for s in specs}
        assert names == {"calculator.add", "calculator.subtract", "calculator.multiply", "calculator.divide", "calculator.sqrt"}

    def test_all_read_only(self):
        toolkit = CalculatorToolkit()
        for spec in toolkit.list_tool_specs():
            assert spec.risk_level.value == "read_only"

    def test_load_tools(self):
        toolkit = CalculatorToolkit()
        tools = toolkit.load_tools()
        assert len(tools) == 5

    def test_add(self):
        toolkit = CalculatorToolkit()
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        assert tools["calculator.add"].handler(2, 3) == 5

    def test_subtract(self):
        toolkit = CalculatorToolkit()
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        assert tools["calculator.subtract"].handler(10, 3) == 7

    def test_multiply(self):
        toolkit = CalculatorToolkit()
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        assert tools["calculator.multiply"].handler(4, 5) == 20

    def test_divide(self):
        toolkit = CalculatorToolkit()
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        assert tools["calculator.divide"].handler(10, 2) == 5.0

    def test_divide_by_zero(self):
        toolkit = CalculatorToolkit()
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        with pytest.raises(ValueError, match="Division by zero"):
            tools["calculator.divide"].handler(1, 0)

    def test_sqrt(self):
        toolkit = CalculatorToolkit()
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        assert tools["calculator.sqrt"].handler(9) == 3.0

    def test_sqrt_negative(self):
        toolkit = CalculatorToolkit()
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        with pytest.raises(ValueError, match="negative"):
            tools["calculator.sqrt"].handler(-1)

    @pytest.mark.asyncio
    async def test_via_registry(self):
        reg = ToolRegistry()
        reg.register_toolkit_loader("calculator", CalculatorToolkit())
        from mtp.protocol import ToolCall, ExecutionPlan, ToolBatch
        plan = ExecutionPlan(batches=[ToolBatch(mode="sequential", calls=[
            ToolCall(id="c1", name="calculator.add", arguments={"a": 10, "b": 20}),
        ])])
        results = await reg.execute_plan(plan)
        assert results[0].success is True
        assert results[0].output == 30

    @pytest.mark.asyncio
    async def test_ref_between_calculator_calls(self):
        reg = ToolRegistry()
        reg.register_toolkit_loader("calculator", CalculatorToolkit())
        from mtp.protocol import ToolCall, ExecutionPlan, ToolBatch
        plan = ExecutionPlan(batches=[
            ToolBatch(mode="sequential", calls=[
                ToolCall(id="c1", name="calculator.add", arguments={"a": 5, "b": 3}),
            ]),
            ToolBatch(mode="sequential", calls=[
                ToolCall(id="c2", name="calculator.multiply", arguments={"a": {"$ref": "c1"}, "b": 2}, depends_on=["c1"]),
            ]),
        ])
        results = await reg.execute_plan(plan)
        assert results[0].output == 8
        assert results[1].output == 16


class TestFileToolkit:
    def test_list_specs(self):
        toolkit = FileToolkit(base_dir="/tmp")
        specs = toolkit.list_tool_specs()
        names = {s.name for s in specs}
        assert names == {"file.list_files", "file.read_file", "file.write_file", "file.search_in_files"}

    def test_write_and_read(self, tmp_path):
        toolkit = FileToolkit(base_dir=tmp_path)
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        tools["file.write_file"].handler("test.txt", "hello world")
        content = tools["file.read_file"].handler("test.txt")
        assert content == "hello world"

    def test_read_with_line_range(self, tmp_path):
        toolkit = FileToolkit(base_dir=tmp_path)
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        tools["file.write_file"].handler("test.txt", "line1\nline2\nline3\n")
        content = tools["file.read_file"].handler("test.txt", start_line=2, end_line=3)
        assert "line2" in content
        assert "line3" in content
        assert "line1" not in content

    def test_list_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        toolkit = FileToolkit(base_dir=tmp_path)
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        result = tools["file.list_files"].handler(".")
        assert "a.txt" in result
        assert "b.txt" in result

    def test_path_escape_blocked(self, tmp_path):
        toolkit = FileToolkit(base_dir=tmp_path)
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        with pytest.raises(ValueError, match="escapes"):
            tools["file.read_file"].handler("../../etc/passwd")

    def test_search_in_files(self, tmp_path):
        (tmp_path / "test.py").write_text("hello world\nfoo bar\nhello again")
        toolkit = FileToolkit(base_dir=tmp_path)
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        results = tools["file.search_in_files"].handler("hello", ".")
        assert len(results) == 2

    def test_write_creates_dirs(self, tmp_path):
        toolkit = FileToolkit(base_dir=tmp_path)
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        tools["file.write_file"].handler("sub/dir/file.txt", "content")
        assert (tmp_path / "sub" / "dir" / "file.txt").read_text() == "content"

    def test_append_mode(self, tmp_path):
        toolkit = FileToolkit(base_dir=tmp_path)
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        tools["file.write_file"].handler("log.txt", "first\n")
        tools["file.write_file"].handler("log.txt", "second\n", append=True)
        content = tools["file.read_file"].handler("log.txt")
        assert "first" in content
        assert "second" in content


class TestShellToolkit:
    def test_list_specs(self):
        toolkit = ShellToolkit()
        specs = toolkit.list_tool_specs()
        assert len(specs) == 1
        assert specs[0].name == "shell.run_command"

    def test_allowed_command(self, tmp_path):
        toolkit = ShellToolkit(base_dir=tmp_path, allowed_commands={"python"})
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        result = tools["shell.run_command"].handler("python --version")
        assert result["returncode"] == 0

    def test_disallowed_command(self, tmp_path):
        toolkit = ShellToolkit(base_dir=tmp_path, allowed_commands={"python"})
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        with pytest.raises(ValueError, match="not allowed"):
            tools["shell.run_command"].handler("rm -rf /")

    @pytest.mark.parametrize("executable", [
        "C:\\Windows\\System32\\whoami.exe", "/usr/bin/python",
        "../python", ".\\python", "C:python", "\\\\server\\share\\python.exe",
    ])
    def test_absolute_path_blocked(self, tmp_path, executable):
        toolkit = ShellToolkit(base_dir=tmp_path, allowed_commands={"python"})
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        with pytest.raises(ValueError, match="bare allowlisted"):
            tools["shell.run_command"].handler(executable)

    def test_empty_command_raises(self, tmp_path):
        toolkit = ShellToolkit(base_dir=tmp_path)
        tools = {t.spec.name: t for t in toolkit.load_tools()}
        with pytest.raises(ValueError, match="Empty"):
            tools["shell.run_command"].handler("")


class TestRegisterLocalToolkits:
    def test_registers_all(self):
        reg = ToolRegistry()
        register_local_toolkits(reg)
        specs = reg.list_tools()
        names = {s.name for s in specs}
        assert "calculator.add" in names
        assert "file.list_files" in names
        assert "python.run_code" in names
        assert "shell.run_command" in names
