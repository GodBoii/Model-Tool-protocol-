from __future__ import annotations

import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from ..protocol import ToolRiskLevel, ToolSpec
from ..runtime import RegisteredTool, ToolRegistry, ToolkitLoader


def _ref_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"$ref": {"type": "string"}},
        "required": ["$ref"],
        "additionalProperties": False,
    }


def _allow_ref(base_schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [base_schema, _ref_schema()]}


class CalculatorToolkit(ToolkitLoader):
    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="calculator.add",
                description="Add two numbers.",
                input_schema={
                    "type": "object",
                    "properties": {"a": _allow_ref({"type": "number"}), "b": _allow_ref({"type": "number"})},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            ),
            ToolSpec(
                name="calculator.subtract",
                description="Subtract b from a.",
                input_schema={
                    "type": "object",
                    "properties": {"a": _allow_ref({"type": "number"}), "b": _allow_ref({"type": "number"})},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            ),
            ToolSpec(
                name="calculator.multiply",
                description="Multiply two numbers.",
                input_schema={
                    "type": "object",
                    "properties": {"a": _allow_ref({"type": "number"}), "b": _allow_ref({"type": "number"})},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            ),
            ToolSpec(
                name="calculator.divide",
                description="Divide a by b.",
                input_schema={
                    "type": "object",
                    "properties": {"a": _allow_ref({"type": "number"}), "b": _allow_ref({"type": "number"})},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            ),
            ToolSpec(
                name="calculator.sqrt",
                description="Square root of a non-negative number.",
                input_schema={
                    "type": "object",
                    "properties": {"x": _allow_ref({"type": "number"})},
                    "required": ["x"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            ),
        ]

    def load_tools(self) -> list[RegisteredTool]:
        def add(a: float, b: float) -> float:
            return a + b

        def subtract(a: float, b: float) -> float:
            return a - b

        def multiply(a: float, b: float) -> float:
            return a * b

        def divide(a: float, b: float) -> float:
            if b == 0:
                raise ValueError("Division by zero.")
            return a / b

        def sqrt(x: float) -> float:
            if x < 0:
                raise ValueError("Square root of negative number.")
            return math.sqrt(x)

        handlers = {
            "calculator.add": add,
            "calculator.subtract": subtract,
            "calculator.multiply": multiply,
            "calculator.divide": divide,
            "calculator.sqrt": sqrt,
        }
        specs = {spec.name: spec for spec in self.list_tool_specs()}
        return [RegisteredTool(spec=specs[name], handler=handler) for name, handler in handlers.items()]


class FileToolkit(ToolkitLoader):
    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir or Path.cwd()).resolve()

    def _resolve(self, path: str) -> Path:
        candidate = (self.base_dir / path).resolve()
        if self.base_dir not in candidate.parents and candidate != self.base_dir:
            raise ValueError("Path escapes base_dir.")
        return candidate

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="file.list_files",
                description="List files and directories under a path.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": _allow_ref({"type": "string"}),
                        "recursive": _allow_ref({"type": "boolean"}),
                    },
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            ),
            ToolSpec(
                name="file.read_file",
                description="Read text content from a file.",
                input_schema={
                    "type": "object",
                    "properties": {"path": _allow_ref({"type": "string"})},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            ),
            ToolSpec(
                name="file.write_file",
                description="Write text to a file under base_dir.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": _allow_ref({"type": "string"}),
                        "content": _allow_ref({"type": "string"}),
                        "append": _allow_ref({"type": "boolean"}),
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.WRITE,
            ),
            ToolSpec(
                name="file.search_in_files",
                description="Search a regex pattern in files under a path.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": _allow_ref({"type": "string"}),
                        "path": _allow_ref({"type": "string"}),
                    },
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            ),
        ]

    def load_tools(self) -> list[RegisteredTool]:
        def list_files(path: str = ".", recursive: bool = False) -> list[str]:
            root = self._resolve(path)
            if not root.exists():
                raise ValueError(f"Path not found: {path}")
            if recursive:
                return [str(p.relative_to(self.base_dir)) for p in root.rglob("*")]
            return [str(p.relative_to(self.base_dir)) for p in root.iterdir()]

        def read_file(path: str) -> str:
            target = self._resolve(path)
            return target.read_text(encoding="utf-8")

        def write_file(path: str, content: str, append: bool = False) -> str:
            target = self._resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with target.open(mode, encoding="utf-8") as fh:
                fh.write(content)
            return str(target.relative_to(self.base_dir))

        def search_in_files(pattern: str, path: str = ".") -> list[dict[str, Any]]:
            root = self._resolve(path)
            regex = re.compile(pattern)
            hits: list[dict[str, Any]] = []
            for file in root.rglob("*"):
                if not file.is_file():
                    continue
                try:
                    content = file.read_text(encoding="utf-8")
                except Exception:
                    continue
                for idx, line in enumerate(content.splitlines(), start=1):
                    if regex.search(line):
                        hits.append(
                            {
                                "file": str(file.relative_to(self.base_dir)),
                                "line": idx,
                                "text": line,
                            }
                        )
            return hits

        handlers = {
            "file.list_files": list_files,
            "file.read_file": read_file,
            "file.write_file": write_file,
            "file.search_in_files": search_in_files,
        }
        specs = {spec.name: spec for spec in self.list_tool_specs()}
        return [RegisteredTool(spec=specs[name], handler=handler) for name, handler in handlers.items()]


class PythonToolkit(ToolkitLoader):
    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir or Path.cwd()).resolve()

    def _safe_builtins(self) -> dict[str, Any]:
        return {
            "abs": abs,
            "bool": bool,
            "dict": dict,
            "float": float,
            "int": int,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "print": print,
            "range": range,
            "set": set,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "tuple": tuple,
        }

    def _resolve(self, path: str) -> Path:
        candidate = (self.base_dir / path).resolve()
        if self.base_dir not in candidate.parents and candidate != self.base_dir:
            raise ValueError("Path escapes base_dir.")
        return candidate

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="python.run_code",
                description="Run Python code in a constrained execution context.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "code": _allow_ref({"type": "string"}),
                        "return_variable": _allow_ref({"type": "string"}),
                    },
                    "required": ["code"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.WRITE,
            ),
            ToolSpec(
                name="python.run_file",
                description="Run a Python file from base_dir and optionally return a variable.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": _allow_ref({"type": "string"}),
                        "return_variable": _allow_ref({"type": "string"}),
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.WRITE,
            ),
        ]

    def load_tools(self) -> list[RegisteredTool]:
        def run_code(code: str, return_variable: str = "result") -> Any:
            globals_ctx = {"__builtins__": self._safe_builtins()}
            locals_ctx: dict[str, Any] = {}
            exec(code, globals_ctx, locals_ctx)
            return locals_ctx.get(return_variable)

        def run_file(path: str, return_variable: str = "result") -> Any:
            target = self._resolve(path)
            code = target.read_text(encoding="utf-8")
            return run_code(code=code, return_variable=return_variable)

        handlers = {
            "python.run_code": run_code,
            "python.run_file": run_file,
        }
        specs = {spec.name: spec for spec in self.list_tool_specs()}
        return [RegisteredTool(spec=specs[name], handler=handler) for name, handler in handlers.items()]


class ShellToolkit(ToolkitLoader):
    def __init__(self, base_dir: str | Path | None = None, timeout_seconds: int = 20) -> None:
        self.base_dir = Path(base_dir or Path.cwd()).resolve()
        self.timeout_seconds = timeout_seconds

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="shell.run_command",
                description="Run a shell command in base_dir and return stdout/stderr.",
                input_schema={
                    "type": "object",
                    "properties": {"command": _allow_ref({"type": "string"})},
                    "required": ["command"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.WRITE,
            )
        ]

    def load_tools(self) -> list[RegisteredTool]:
        def run_command(command: str) -> dict[str, Any]:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(self.base_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            return {
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }

        return [
            RegisteredTool(
                spec=self.list_tool_specs()[0],
                handler=run_command,
            )
        ]


def register_local_toolkits(
    registry: ToolRegistry,
    *,
    base_dir: str | Path | None = None,
) -> None:
    root = Path(base_dir or os.getcwd()).resolve()
    registry.register_toolkit_loader("calculator", CalculatorToolkit())
    registry.register_toolkit_loader("file", FileToolkit(base_dir=root))
    registry.register_toolkit_loader("python", PythonToolkit(base_dir=root))
    registry.register_toolkit_loader("shell", ShellToolkit(base_dir=root))
