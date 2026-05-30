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

    def _is_under_base(self, candidate: Path) -> bool:
        try:
            candidate.resolve(strict=False).relative_to(self.base_dir)
            return True
        except ValueError:
            return False

    def _resolve(self, path: str) -> Path:
        candidate = (self.base_dir / path).resolve(strict=False)
        if not self._is_under_base(candidate):
            raise ValueError("Path escapes base_dir.")
        return candidate

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="file.list_files",
                description="List files and directories under a path. Automatically ignores common hidden/build directories.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": allow_ref({"type": "string"}),
                        "recursive": allow_ref({"type": "boolean"}),
                        "limit": allow_ref({"type": "integer", "description": "Maximum number of results to return (default 1000)"}),
                    },
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            ),
            ToolSpec(
                name="file.read_file",
                description="Read text content from a file. Large files are truncated unless a specific line range is requested.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": allow_ref({"type": "string"}),
                        "start_line": allow_ref({"type": "integer", "description": "1-indexed start line"}),
                        "end_line": allow_ref({"type": "integer", "description": "1-indexed end line (inclusive)"})
                    },
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
                description="Search a regex pattern in files under a path. Automatically ignores hidden/build directories to prevent context bloat.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": allow_ref({"type": "string"}),
                        "path": allow_ref({"type": "string"}),
                        "max_results": allow_ref({"type": "integer", "description": "Maximum number of matches to return (default 50)"}),
                    },
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            ),
        ]

    def load_tools(self) -> list[RegisteredTool]:
        ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", "dist", "build", ".pytest_cache"}

        def list_files(path: str = ".", recursive: bool = False, limit: int = 1000) -> list[str]:
            root = self._resolve(path)
            if not root.exists():
                raise ValueError(f"Path not found: {path}")
            
            results = []
            if recursive:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith('.')]
                    for d in dirnames:
                        rel = str((Path(dirpath) / d).relative_to(self.base_dir))
                        results.append(f"{rel}/")
                        if len(results) >= limit:
                            results.append(f"... (truncated at {limit} items)")
                            return results
                    for f in filenames:
                        rel = str((Path(dirpath) / f).relative_to(self.base_dir))
                        results.append(rel)
                        if len(results) >= limit:
                            results.append(f"... (truncated at {limit} items)")
                            return results
                return results
            else:
                for p in root.iterdir():
                    if p.is_dir():
                        if p.name in ignore_dirs or p.name.startswith('.'):
                            continue
                        results.append(f"{str(p.relative_to(self.base_dir))}/")
                    else:
                        results.append(str(p.relative_to(self.base_dir)))
                    if len(results) >= limit:
                        results.append(f"... (truncated at {limit} items)")
                        break
                return results

        def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
            target = self._resolve(path)
            if target.stat().st_size > 10 * 1024 * 1024:  # 10MB
                raise ValueError(f"File is too large to read (over 10MB). Size: {target.stat().st_size} bytes.")
                
            content = target.read_text(encoding="utf-8")
            
            if start_line is not None or end_line is not None:
                lines = content.splitlines(keepends=True)
                start = max(0, (start_line - 1)) if start_line is not None else 0
                end = min(len(lines), end_line) if end_line is not None else len(lines)
                if start >= len(lines):
                    return ""
                return "".join(lines[start:end])
                
            lines = content.splitlines(keepends=True)
            if len(lines) > 2000:
                warning = f"\n\n[WARNING: File too long ({len(lines)} lines). Only first 1000 lines shown. Use start_line and end_line to view more.]"
                return "".join(lines[:1000]) + warning
                
            return content

        def write_file(path: str, content: str, append: bool = False) -> str:
            target = self._resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with target.open(mode, encoding="utf-8") as fh:
                fh.write(content)
            return str(target.relative_to(self.base_dir))

        def search_in_files(pattern: str, path: str = ".", max_results: int = 50) -> list[dict[str, Any]]:
            root = self._resolve(path)
            try:
                regex = re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern: {e}")
                
            hits: list[dict[str, Any]] = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith('.')]
                
                for filename in filenames:
                    file = Path(dirpath) / filename
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
                                    "text": line.strip(),
                                }
                            )
                            if len(hits) >= max_results:
                                hits.append({"warning": f"Search truncated at {max_results} results. Please refine your pattern or path."})
                                return hits
            return hits

        handlers = {
            "file.list_files": list_files,
            "file.read_file": read_file,
            "file.write_file": write_file,
            "file.search_in_files": search_in_files,
        }
        specs = {spec.name: spec for spec in self.list_tool_specs()}
        return [RegisteredTool(spec=specs[name], handler=handler) for name, handler in handlers.items()]
