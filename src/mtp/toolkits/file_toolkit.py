from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..protocol import ToolRiskLevel, ToolSpec
from ..runtime import RegisteredTool, ToolkitLoader
from .common import allow_ref


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
                        "path": allow_ref({"type": "string"}),
                        "recursive": allow_ref({"type": "boolean"}),
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
                    "properties": {"path": allow_ref({"type": "string"})},
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
                        "path": allow_ref({"type": "string"}),
                        "content": allow_ref({"type": "string"}),
                        "append": allow_ref({"type": "boolean"}),
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
                        "pattern": allow_ref({"type": "string"}),
                        "path": allow_ref({"type": "string"}),
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
