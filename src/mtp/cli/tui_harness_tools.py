from __future__ import annotations

import ast
import fnmatch
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from mtp.protocol import ToolRiskLevel, ToolSpec
from mtp.runtime import RegisteredTool, ToolkitLoader
from mtp.codebase import CodebaseMemory


_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt", ".toml", ".yaml", ".yml",
    ".css", ".html", ".sql", ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".sh",
    ".ps1", ".bat", ".ini", ".cfg",
}
_DEFAULT_IGNORES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build",
    ".mypy_cache", ".ruff_cache", ".next", ".turbo",
}
def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _query_schema(*, include_path: bool = True, include_limit: bool = True) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "query": {"type": "string"},
        "pattern": {"type": "string"},
    }
    if include_path:
        properties["path"] = {"type": "string"}
    if include_limit:
        properties["limit"] = {"type": "integer"}
    return _schema(properties)


class _Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def resolve(self, path: str = ".") -> Path:
        target = (self.root / path).resolve(strict=False)
        base = os.path.normcase(str(self.root))
        normalized = os.path.normcase(str(target))
        if os.path.commonpath([base, normalized]) != base:
            raise ValueError("Path escapes workspace.")
        return target

    def rel(self, path: Path) -> str:
        return str(path.resolve(strict=False).relative_to(self.root)).replace("\\", "/")

    def is_ignored(self, path: Path) -> bool:
        try:
            parts = path.resolve(strict=False).relative_to(self.root).parts
        except ValueError:
            parts = path.parts
        return any(part in _DEFAULT_IGNORES for part in parts)

    def root_entries(self) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for item in sorted(self.root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file"})
        return entries

    def text_files(self, path: str = ".") -> list[Path]:
        start = self.resolve(path)
        if start.is_file():
            return [start]
        files: list[Path] = []
        for item in start.rglob("*"):
            if item.is_file() and not self.is_ignored(item) and item.suffix.lower() in _TEXT_EXTENSIONS:
                files.append(item)
        return files


class ContextToolkit(ToolkitLoader):
    def __init__(self, root: str | Path) -> None:
        self.ws = _Workspace(root)

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec("project.inspect", "Inspect the workspace root, count source file formats, and report concise git status. Returns counts only for files, never a recursive file list.", _schema({}), risk_level=ToolRiskLevel.READ_ONLY, cache_ttl_seconds=15),
            ToolSpec("fs.search", "Find relevant files by word, text, fuzzy, and lightweight semantic matching. Uses indexed workspace memory when available. Use `query` for the search text; `pattern` is accepted as an alias.", _query_schema(), risk_level=ToolRiskLevel.READ_ONLY),
            ToolSpec("fs.grep", "Search indexed workspace memory first, then live files, and return relevant matching snippets. Use `query` for the search text; `pattern` is accepted as an alias.", _query_schema(), risk_level=ToolRiskLevel.READ_ONLY),
            ToolSpec("fs.read_text", "Read a bounded line window from a workspace text file by relative path.", _schema({"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]), risk_level=ToolRiskLevel.READ_ONLY),
            ToolSpec("agent.explore_codebase", "Subagent-style deep codebase search. Use for broad grep and locating relevant files. Use `query` for the search text; `pattern` is accepted as an alias.", _schema({"task": {"type": "string"}, "query": {"type": "string"}, "pattern": {"type": "string"}, "limit": {"type": "integer"}}, ["task"]), risk_level=ToolRiskLevel.READ_ONLY),
            ToolSpec("agent.debug_context", "Subagent-style debug context gatherer: project summary, git diff, and likely files. Use `query` for the search text; `pattern` is accepted as an alias.", _schema({"symptom": {"type": "string"}, "query": {"type": "string"}, "pattern": {"type": "string"}}, ["symptom"]), risk_level=ToolRiskLevel.READ_ONLY),
        ]

    def load_tools(self) -> list[RegisteredTool]:
        specs = {spec.name: spec for spec in self.list_tool_specs()}

        def project_inspect() -> dict[str, Any]:
            extensions: dict[str, int] = {}
            total_files = 0
            for item in self.ws.text_files("."):
                if _looks_secret(self.ws.rel(item)):
                    continue
                total_files += 1
                extensions[item.suffix.lower() or "(none)"] = extensions.get(item.suffix.lower() or "(none)", 0) + 1
            git = _run(["git", "status", "--short"], self.ws.root, timeout=8)
            memory_status = CodebaseMemory(self.ws.root).status()
            return {
                "root": str(self.ws.root),
                "root_structure": self.ws.root_entries(),
                "file_counts": {
                    "total_text_files": total_files,
                    "by_extension": dict(sorted(extensions.items(), key=lambda kv: (-kv[1], kv[0]))),
                },
                "git_status": git.get("stdout", "")[:4000],
                "codebase_memory": {
                    "enabled": memory_status.enabled,
                    "files": memory_status.file_count,
                    "chunks": memory_status.chunk_count,
                    "conversation_summaries": memory_status.summary_count,
                    "last_scan_at": memory_status.last_scan_at,
                },
            }

        def fs_read_text(path: str, start_line: int = 1, end_line: int = 240) -> dict[str, Any]:
            target = self.ws.resolve(path)
            rel = self.ws.rel(target)
            if _looks_secret(rel):
                raise ValueError("Reading secret-like files is blocked by the TUI harness.")
            if not target.is_file():
                raise ValueError("Path is not a file.")
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            start = max(1, int(start_line))
            end = max(start, int(end_line))
            end = min(end, start + 239, total)
            content = "\n".join(lines[start - 1:end])
            return {
                "file": rel,
                "start_line": start,
                "end_line": end,
                "total_lines": total,
                "truncated": start > 1 or end < total,
                "content": content,
            }

        def fs_search(query: str | None = None, path: str = ".", limit: int = 80, pattern: str | None = None) -> dict[str, Any]:
            query = (query or pattern or "").strip()
            if not query:
                raise ValueError("fs.search requires `query` (or alias `pattern`).")
            memory = CodebaseMemory(self.ws.root)
            if path in {".", "", None} and memory.is_enabled():
                memory_hits = memory.search(query, limit=limit)
                if memory_hits:
                    return {
                        "query": query,
                        "source": "codebase_memory",
                        "count": len(memory_hits),
                        "total_matches": len(memory_hits),
                        "truncated": False,
                        "hits": [
                            {
                                "file": hit.file,
                                "score": hit.score,
                                "match": hit.match,
                                "start_line": hit.start_line,
                                "end_line": hit.end_line,
                                "kind": hit.kind,
                                "snippets": [hit.snippet],
                            }
                            for hit in memory_hits
                        ],
                    }
            fast_hits = _search_with_ripgrep(self.ws, query=query, path=path, limit=limit)
            if fast_hits is not None:
                return fast_hits
            terms = _search_terms(query)
            query_norm = _normalize_text(query)
            hits: list[dict[str, Any]] = []
            for file in self.ws.text_files(path):
                if _looks_secret(self.ws.rel(file)):
                    continue
                try:
                    text = file.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                rel = self.ws.rel(file)
                score, reasons, snippets = _score_search_match(query_norm, terms, rel, text)
                if score <= 0:
                    continue
                hits.append({"file": rel, "score": round(score, 3), "match": reasons, "snippets": snippets[:3]})
            hits.sort(key=lambda hit: (-hit["score"], hit["file"]))
            bounded = hits[: max(1, int(limit))]
            return {
                "query": query,
                "terms": terms,
                "count": len(bounded),
                "total_matches": len(hits),
                "truncated": len(hits) > len(bounded),
                "hits": bounded,
            }

        def fs_grep(query: str | None = None, path: str = ".", limit: int = 80, pattern: str | None = None) -> dict[str, Any]:
            return fs_search(query=query, path=path, limit=limit, pattern=pattern)

        def explore_codebase(task: str, query: str = "", limit: int = 120, pattern: str = "") -> dict[str, Any]:
            probe = query or pattern or _keywords(task)
            return {"task": task, "query": probe, "hits": fs_search(probe, limit=limit), "project": project_inspect()}

        def debug_context(symptom: str, query: str = "", pattern: str = "") -> dict[str, Any]:
            probe = query or pattern or _keywords(symptom)
            diff = _run(["git", "diff", "--", "."], self.ws.root, timeout=8).get("stdout", "")
            return {"symptom": symptom, "project": project_inspect(), "hits": fs_search(probe, limit=80), "git_diff": diff[:12000]}

        handlers = {
            "project.inspect": project_inspect,
            "fs.read_text": fs_read_text,
            "fs.search": fs_search,
            "fs.grep": fs_grep,
            "agent.explore_codebase": explore_codebase,
            "agent.debug_context": debug_context,
        }
        return [RegisteredTool(spec=specs[name], handler=handler) for name, handler in handlers.items()]


class EditToolkit(ToolkitLoader):
    def __init__(self, root: str | Path) -> None:
        self.ws = _Workspace(root)

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec("edit.apply_patch", "Main-agent edit tool. Replace an exact old_text block with new_text and return a diff.", _schema({"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}, "replace_all": {"type": "boolean"}}, ["path", "old_text", "new_text"]), risk_level=ToolRiskLevel.WRITE),
            ToolSpec("edit.create_file", "Create a new text file if it does not already exist.", _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]), risk_level=ToolRiskLevel.WRITE),
        ]

    def load_tools(self) -> list[RegisteredTool]:
        import difflib

        specs = {spec.name: spec for spec in self.list_tool_specs()}

        def apply_patch(path: str, old_text: str, new_text: str, replace_all: bool = False) -> dict[str, Any]:
            target = self.ws.resolve(path)
            before = target.read_text(encoding="utf-8", errors="replace")
            count = before.count(old_text)
            if count == 0:
                raise ValueError("old_text was not found exactly. Re-read the file and try a smaller exact block.")
            if count > 1 and not replace_all:
                raise ValueError(f"old_text appears {count} times. Set replace_all=true or choose a more specific block.")
            after = before.replace(old_text, new_text) if replace_all else before.replace(old_text, new_text, 1)
            target.write_text(after, encoding="utf-8")
            diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=f"a/{path}", tofile=f"b/{path}"))
            return {
                "file": self.ws.rel(target),
                "replacements": count if replace_all else 1,
                "diff": diff[:20000],
                "new_text": new_text[:20000],
            }

        def create_file(path: str, content: str) -> dict[str, Any]:
            target = self.ws.resolve(path)
            if target.exists():
                raise ValueError("Refusing to overwrite existing file.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {
                "file": self.ws.rel(target),
                "bytes": len(content.encode("utf-8")),
                "content": content[:20000],
            }

        return [
            RegisteredTool(spec=specs["edit.apply_patch"], handler=apply_patch),
            RegisteredTool(spec=specs["edit.create_file"], handler=create_file),
        ]


class CommandToolkit(ToolkitLoader):
    def __init__(self, root: str | Path) -> None:
        self.ws = _Workspace(root)

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec("shell.run", "Run a workspace command through the harness permission layer.", _schema({"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, ["command"]), risk_level=ToolRiskLevel.WRITE),
            ToolSpec("git.status", "Return concise git status.", _schema({}), risk_level=ToolRiskLevel.READ_ONLY),
            ToolSpec("git.diff", "Return git diff for the workspace or one path.", _schema({"path": {"type": "string"}, "max_chars": {"type": "integer"}}), risk_level=ToolRiskLevel.READ_ONLY),
            ToolSpec("test.run", "Run a targeted test command after edits.", _schema({"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, ["command"]), risk_level=ToolRiskLevel.WRITE),
            ToolSpec("agent.syntax_check", "Subagent-style syntax/lint check for Python files without editing.", _schema({"path": {"type": "string"}}), risk_level=ToolRiskLevel.READ_ONLY),
        ]

    def load_tools(self) -> list[RegisteredTool]:
        specs = {spec.name: spec for spec in self.list_tool_specs()}

        def shell_run(command: str, timeout_seconds: int = 60) -> dict[str, Any]:
            return _run_shell(command, self.ws.root, timeout_seconds)

        def git_status() -> dict[str, Any]:
            return _run(["git", "status", "--short", "--branch"], self.ws.root, timeout=10)

        def git_diff(path: str = ".", max_chars: int = 20000) -> str:
            cmd = ["git", "diff", "--", path]
            return _run(cmd, self.ws.root, timeout=10).get("stdout", "")[: max(1000, int(max_chars))]

        def test_run(command: str, timeout_seconds: int = 120) -> dict[str, Any]:
            compact = " ".join(command.split())
            allowed = ("pytest", "python -m pytest", "npm test", "npm run test", "python -m compileall", "python -m py_compile")
            if not compact.startswith(allowed):
                raise ValueError(f"Use a targeted test command. Allowed prefixes: {', '.join(allowed)}")
            return _run_shell(command, self.ws.root, timeout_seconds)

        def syntax_check(path: str = ".") -> dict[str, Any]:
            target = self.ws.resolve(path)
            files = [target] if target.is_file() else [p for p in self.ws.text_files(path) if p.suffix == ".py"]
            errors = []
            for file in files[:300]:
                try:
                    ast.parse(file.read_text(encoding="utf-8", errors="replace"), filename=str(file))
                except SyntaxError as exc:
                    errors.append({"file": self.ws.rel(file), "line": exc.lineno, "message": exc.msg})
            return {"checked": len(files[:300]), "errors": errors}

        handlers = {
            "shell.run": shell_run,
            "git.status": git_status,
            "git.diff": git_diff,
            "test.run": test_run,
            "agent.syntax_check": syntax_check,
        }
        return [RegisteredTool(spec=specs[name], handler=handler) for name, handler in handlers.items()]


def register_harness_toolkits(registry: Any, *, root: str | Path) -> None:
    registry.register_toolkit_loader("project", ContextToolkit(root))
    registry.register_toolkit_loader("fs", ContextToolkit(root))
    registry.register_toolkit_loader("agent", ContextToolkit(root))
    registry.register_toolkit_loader("edit", EditToolkit(root))
    registry.register_toolkit_loader("shell", CommandToolkit(root))
    registry.register_toolkit_loader("git", CommandToolkit(root))
    registry.register_toolkit_loader("test", CommandToolkit(root))


def _looks_secret(path: str) -> bool:
    name = Path(path).name.lower()
    return name == ".env" or fnmatch.fnmatch(name, "*.env") or fnmatch.fnmatch(name, "*.env.*")


def _keywords(text: str) -> str:
    words = [w.strip(".,:;()[]{}\"'`").lower() for w in text.split()]
    useful = [w for w in words if len(w) >= 4 and w not in {"that", "this", "with", "from", "when", "where", "there"}]
    return useful[0] if useful else text[:40]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _search_terms(query: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_./-]+", query.lower())
    stop = {"the", "and", "for", "from", "that", "this", "with", "what", "where", "when", "into"}
    terms = [word for word in words if len(word) >= 2 and word not in stop]
    return terms or [query.lower().strip()]


def _score_search_match(query_norm: str, terms: list[str], rel_path: str, text: str) -> tuple[float, list[str], list[str]]:
    text_norm = _normalize_text(text)
    rel_norm = rel_path.lower()
    score = 0.0
    reasons: list[str] = []
    snippets: list[str] = []

    if query_norm and query_norm in text_norm:
        score += 8
        reasons.append("text")
    if query_norm and query_norm in rel_norm:
        score += 5
        reasons.append("path")

    lines = text.splitlines()
    for term in terms:
        term_rx = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        fuzzy_rx = re.compile(".*?".join(re.escape(ch) for ch in term), re.IGNORECASE) if len(term) >= 4 else None
        term_hit = False
        fuzzy_hit = False
        for line_no, line in enumerate(lines, start=1):
            if term_rx.search(line):
                term_hit = True
                if len(snippets) < 3:
                    snippets.append(f"{line_no}: {line.strip()[:240]}")
            elif fuzzy_rx and fuzzy_rx.search(line):
                fuzzy_hit = True
                if len(snippets) < 2:
                    snippets.append(f"{line_no}: {line.strip()[:240]}")
        if term in rel_norm:
            score += 4
            reasons.append(f"path:{term}")
        if term_hit:
            score += 3
            reasons.append(f"word:{term}")
        elif fuzzy_hit:
            score += 1
            reasons.append(f"fuzzy:{term}")

    # Lightweight semantic signal: rank files higher when several query words co-occur,
    # even if the full phrase is not present.
    unique_term_hits = {term for term in terms if term in text_norm or term in rel_norm}
    if len(unique_term_hits) >= 2:
        score += len(unique_term_hits) * 1.5
        reasons.append("semantic:term_cooccurrence")

    return score, list(dict.fromkeys(reasons)), snippets


def _run(cmd: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return {"returncode": completed.returncode, "stdout": completed.stdout.strip(), "stderr": completed.stderr.strip()}
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


def _run_shell(command: str, cwd: Path, timeout: int) -> dict[str, Any]:
    completed = subprocess.run(command, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=max(1, int(timeout)))
    return {"returncode": completed.returncode, "stdout": completed.stdout.strip()[:30000], "stderr": completed.stderr.strip()[:12000]}


def _search_with_ripgrep(ws: _Workspace, *, query: str, path: str, limit: int) -> dict[str, Any] | None:
    rg_bin = shutil.which("rg")
    if not rg_bin:
        return None

    search_root = ws.resolve(path or ".")
    if search_root.is_file():
        search_targets = [str(search_root)]
    else:
        search_targets = [str(search_root)]

    base_cmd = [
        rg_bin,
        "--line-number",
        "--smart-case",
        "--max-count",
        "3",
        "--max-columns",
        "240",
        "--glob",
        "!**/.git/**",
        "--glob",
        "!**/node_modules/**",
        "--glob",
        "!**/.venv/**",
        "--glob",
        "!**/venv/**",
        "--glob",
        "!**/__pycache__/**",
        query,
        *search_targets,
    ]
    try:
        completed = subprocess.run(
            base_cmd,
            cwd=str(ws.root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None

    hits_by_file: dict[str, dict[str, Any]] = {}
    try:
        file_list = subprocess.run(
            [rg_bin, "--files", *search_targets],
            cwd=str(ws.root),
            capture_output=True,
            text=True,
            timeout=8,
        )
        query_terms = _search_terms(query)
        for raw_file in file_list.stdout.splitlines():
            normalized = raw_file.replace("\\", "/")
            if _looks_secret(normalized):
                continue
            if any(term in normalized.lower() for term in query_terms):
                entry = hits_by_file.setdefault(
                    normalized,
                    {"file": normalized, "score": 6.0, "match": ["path"], "snippets": [f"path: {normalized}"]},
                )
                entry["score"] = max(float(entry["score"]), 6.0)
    except Exception:
        pass

    for line in completed.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        raw_file, line_no, snippet = parts
        try:
            rel_file = ws.rel(Path(raw_file) if Path(raw_file).is_absolute() else ws.root / raw_file)
        except Exception:
            rel_file = raw_file.replace("\\", "/")
        if _looks_secret(rel_file):
            continue
        entry = hits_by_file.setdefault(
            rel_file,
            {"file": rel_file, "score": 0.0, "match": ["rg"], "snippets": []},
        )
        entry["score"] += 5.0
        if len(entry["snippets"]) < 3:
            entry["snippets"].append(f"{line_no}: {snippet.strip()[:240]}")

    if not hits_by_file:
        return None

    hits = sorted(hits_by_file.values(), key=lambda item: (-float(item["score"]), str(item["file"])))
    bounded = hits[: max(1, int(limit))]
    return {
        "query": query,
        "source": "ripgrep",
        "count": len(bounded),
        "total_matches": len(hits),
        "truncated": len(hits) > len(bounded),
        "hits": bounded,
    }

