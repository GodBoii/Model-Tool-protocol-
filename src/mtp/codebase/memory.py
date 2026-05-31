from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any


_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt", ".toml",
    ".yaml", ".yml", ".css", ".html", ".sql", ".rs", ".go", ".java", ".c",
    ".cpp", ".h", ".hpp", ".sh", ".ps1", ".bat", ".ini", ".cfg", ".rb",
    ".php", ".swift", ".kt", ".kts", ".scala", ".cs", ".vue", ".svelte",
}
_IGNORE_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".env", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".nox", ".next", ".nuxt", ".turbo", ".cache", ".parcel-cache", "dist",
    "build", "coverage", ".coverage", ".idea", ".vscode", "target", "out",
    "tmp", "temp", ".mtp",
}
_SECRET_NAMES = {
    ".env", ".npmrc", ".pypirc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}
_SECRET_SUFFIXES = {
    ".pem", ".key", ".crt", ".p12", ".pfx", ".sqlite", ".db", ".db3",
}
_MAX_FILE_BYTES = 512 * 1024
_CHUNK_LINES = 120
_CHUNK_OVERLAP = 20
_VECTOR_DIMS = 128


@dataclass(slots=True)
class ScanStats:
    root: Path
    files_seen: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    files_deleted: int = 0
    chunks_indexed: int = 0
    changed_files: int = 0
    percent: int = 0


@dataclass(slots=True)
class CodebaseMemoryStatus:
    root: Path
    db_path: Path
    enabled: bool
    file_count: int
    chunk_count: int
    summary_count: int
    last_scan_at: str | None
    schema_version: int


@dataclass(slots=True)
class SearchHit:
    file: str
    score: float
    start_line: int
    end_line: int
    kind: str
    match: list[str]
    snippet: str


@dataclass(slots=True)
class ConversationSummary:
    session_id: str
    title: str
    summary: str
    created_at: str


class CodebaseMemory:
    """Persistent codebase index stored under ``<project>/.mtp/memory``."""

    schema_version = 1
    _lock_guard = threading.Lock()
    _scan_locks: dict[str, threading.Lock] = {}

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.memory_dir = self.root / ".mtp" / "memory"
        self.db_path = self.memory_dir / "codebase.sqlite"

    @classmethod
    def discover_root(cls, path: str | Path | None = None) -> Path:
        start = Path(path or Path.cwd()).expanduser().resolve()
        current = start if start.is_dir() else start.parent
        for candidate in (current, *current.parents):
            if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists() or (candidate / "package.json").exists():
                return candidate
        return current

    def initialize(self, *, enabled: bool = True) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._ensure_schema(conn)
            self._set_meta(conn, "enabled", "1" if enabled else "0")
            self._set_meta(conn, "schema_version", str(self.schema_version))
            self._set_meta(conn, "root", str(self.root))

    def set_enabled(self, enabled: bool) -> None:
        self.initialize(enabled=enabled)

    def is_enabled(self) -> bool:
        if not self.db_path.exists():
            return False
        with self._connect() as conn:
            self._ensure_schema(conn)
            return self._get_meta(conn, "enabled") == "1"

    def status(self) -> CodebaseMemoryStatus:
        if not self.db_path.exists():
            return CodebaseMemoryStatus(
                root=self.root,
                db_path=self.db_path,
                enabled=False,
                file_count=0,
                chunk_count=0,
                summary_count=0,
                last_scan_at=None,
                schema_version=self.schema_version,
            )
        with self._connect() as conn:
            self._ensure_schema(conn)
            return CodebaseMemoryStatus(
                root=self.root,
                db_path=self.db_path,
                enabled=self._get_meta(conn, "enabled") == "1",
                file_count=self._count(conn, "files"),
                chunk_count=self._count(conn, "chunks"),
                summary_count=self._count(conn, "conversation_summaries"),
                last_scan_at=self._get_meta(conn, "last_scan_at"),
                schema_version=int(self._get_meta(conn, "schema_version") or self.schema_version),
            )

    def show(self, *, limit: int = 8) -> dict[str, Any]:
        status = self.status()
        if not self.db_path.exists():
            return {
                "root": str(status.root),
                "db_path": str(status.db_path),
                "enabled": status.enabled,
                "db_size_bytes": 0,
                "files": status.file_count,
                "chunks": status.chunk_count,
                "summaries": status.summary_count,
                "last_scan_at": status.last_scan_at,
                "languages": [],
                "chunk_kinds": [],
                "largest_files": [],
                "recent_summaries": [],
            }
        with self._connect() as conn:
            self._ensure_schema(conn)
            languages = [
                {
                    "language": str(language or "(none)"),
                    "files": int(file_count or 0),
                    "lines": int(line_count or 0),
                }
                for language, file_count, line_count in conn.execute(
                    """
                    select coalesce(language, '(none)'), count(*), coalesce(sum(line_count), 0)
                    from files
                    group by language
                    order by count(*) desc, language asc
                    limit ?
                    """,
                    (max(1, int(limit)),),
                )
            ]
            chunk_kinds = [
                {"kind": str(kind), "count": int(count or 0)}
                for kind, count in conn.execute(
                    """
                    select kind, count(*)
                    from chunks
                    group by kind
                    order by count(*) desc, kind asc
                    limit ?
                    """,
                    (max(1, int(limit)),),
                )
            ]
            largest_files = [
                {
                    "path": str(path),
                    "size": int(size or 0),
                    "lines": int(line_count or 0),
                    "language": str(language or "(none)"),
                }
                for path, size, line_count, language in conn.execute(
                    """
                    select path, size, line_count, coalesce(language, '(none)')
                    from files
                    order by size desc, path asc
                    limit ?
                    """,
                    (max(1, int(limit)),),
                )
            ]
            recent_summaries = [
                {
                    "title": str(title),
                    "created_at": str(created_at),
                    "model": str(model or ""),
                }
                for title, created_at, model in conn.execute(
                    """
                    select title, created_at, model
                    from conversation_summaries
                    order by id desc
                    limit ?
                    """,
                    (max(1, int(limit)),),
                )
            ]
        return {
            "root": str(status.root),
            "db_path": str(status.db_path),
            "enabled": status.enabled,
            "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "files": status.file_count,
            "chunks": status.chunk_count,
            "summaries": status.summary_count,
            "last_scan_at": status.last_scan_at,
            "languages": languages,
            "chunk_kinds": chunk_kinds,
            "largest_files": largest_files,
            "recent_summaries": recent_summaries,
        }

    def scan(
        self,
        *,
        enable: bool = True,
        progress: Callable[[ScanStats], None] | None = None,
    ) -> ScanStats:
        stats = ScanStats(root=self.root)
        with self._workspace_scan_lock():
            candidates = list(self._iter_candidate_files())
            total = max(len(candidates), 1)
            seen_paths: set[str] = set()
            with self._connect() as conn:
                self._ensure_schema(conn)
                self._set_meta(conn, "schema_version", str(self.schema_version))
                self._set_meta(conn, "root", str(self.root))
                for idx, path in enumerate(candidates, start=1):
                    stats.files_seen += 1
                    rel = self._rel(path)
                    seen_paths.add(rel)
                    stats.percent = min(99, int((idx / total) * 100))
                    try:
                        indexed = self._index_file(conn, path, rel)
                    except Exception:
                        stats.files_skipped += 1
                        self._emit(progress, stats)
                        continue
                    if indexed:
                        stats.changed_files += 1
                    stats.files_indexed += 1
                    self._emit(progress, stats)

                existing = [row[0] for row in conn.execute("select path from files")]
                for rel in existing:
                    if rel not in seen_paths:
                        conn.execute("delete from chunks where path = ?", (rel,))
                        conn.execute("delete from files where path = ?", (rel,))
                        stats.files_deleted += 1

                stats.chunks_indexed = self._count(conn, "chunks")
                stats.percent = 100
                self._set_meta(conn, "last_scan_at", _utc_now())
                self._set_meta(conn, "enabled", "1" if enable else "0")
                conn.commit()
        self._emit(progress, stats)
        return stats

    def refresh_changed(self) -> ScanStats:
        if not self.is_enabled():
            return ScanStats(root=self.root, percent=100)
        return self.scan(enable=True)

    def search(self, query: str, *, limit: int = 20, refresh: bool = True) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []
        if not self.db_path.exists():
            return []
        q_terms = _tokens(query)
        q_vec = _embed(query)
        hits: list[SearchHit] = []
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "select path, start_line, end_line, kind, text, vector_json from chunks"
            ).fetchall()
        for path, start, end, kind, text, vector_json in rows:
            score, reasons = _score_hit(query, q_terms, q_vec, path, text, vector_json)
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    file=str(path),
                    score=round(score, 3),
                    start_line=int(start),
                    end_line=int(end),
                    kind=str(kind),
                    match=reasons,
                    snippet=_snippet(text, q_terms),
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.file, hit.start_line))
        return hits[: max(1, int(limit))]

    def record_conversation_summary(
        self,
        *,
        session_id: str,
        prompt: str,
        response: str,
        backend: str | None = None,
        model: str | None = None,
    ) -> ConversationSummary | None:
        if not self.is_enabled():
            return None
        prompt_clean = _compact(prompt, 900)
        response_clean = _compact(response, 1400)
        title = _compact(prompt_clean, 80) or "conversation summary"
        summary_parts = [
            "Conversation summary",
            f"User asked: {prompt_clean}",
            f"Assistant did: {response_clean}",
        ]
        if backend or model:
            summary_parts.append(f"Backend/model: {backend or 'unknown'} / {model or 'unknown'}")
        summary = "\n".join(summary_parts)
        created_at = _utc_now()
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                insert into conversation_summaries
                    (session_id, title, prompt, summary, backend, model, created_at, vector_json)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    title,
                    prompt_clean,
                    summary,
                    backend,
                    model,
                    created_at,
                    json.dumps(_embed(summary), separators=(",", ":")),
                ),
            )
            conn.commit()
        return ConversationSummary(session_id=session_id, title=title, summary=summary, created_at=created_at)

    def _connect(self) -> sqlite3.Connection:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("pragma journal_mode = wal")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("create table if not exists metadata (key text primary key, value text)")
        conn.execute(
            """
            create table if not exists files (
                path text primary key,
                size integer not null,
                mtime_ns integer not null,
                sha256 text not null,
                language text,
                line_count integer not null,
                indexed_at text not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists chunks (
                id integer primary key autoincrement,
                path text not null,
                start_line integer not null,
                end_line integer not null,
                kind text not null,
                text text not null,
                vector_json text not null
            )
            """
        )
        conn.execute("create index if not exists idx_chunks_path on chunks(path)")
        conn.execute(
            """
            create table if not exists conversation_summaries (
                id integer primary key autoincrement,
                session_id text not null,
                title text not null,
                prompt text not null,
                summary text not null,
                backend text,
                model text,
                created_at text not null,
                vector_json text not null
            )
            """
        )
        self._set_meta(conn, "schema_version", str(self.schema_version))
        conn.commit()

    def _get_meta(self, conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("select value from metadata where key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "insert into metadata(key, value) values(?, ?) on conflict(key) do update set value = excluded.value",
            (key, value),
        )

    def _count(self, conn: sqlite3.Connection, table: str) -> int:
        row = conn.execute(f"select count(*) from {table}").fetchone()
        return int(row[0] or 0)

    def _iter_candidate_files(self) -> list[Path]:
        files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            base = Path(dirpath)
            dirnames[:] = [
                name for name in dirnames
                if not _is_ignored_dir(name)
            ]
            for filename in filenames:
                path = base / filename
                if self._should_index(path):
                    files.append(path)
        files.sort(key=lambda p: self._rel(p))
        return files

    def _should_index(self, path: Path) -> bool:
        try:
            rel_parts = path.resolve(strict=False).relative_to(self.root).parts
        except ValueError:
            return False
        if any(part in _IGNORE_DIRS for part in rel_parts):
            return False
        name = path.name.lower()
        if name in _SECRET_NAMES or name.endswith(".env") or ".env." in name:
            return False
        if path.suffix.lower() in _SECRET_SUFFIXES:
            return False
        if path.suffix.lower() not in _TEXT_EXTENSIONS:
            return False
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                return False
        except OSError:
            return False
        return True

    def _index_file(self, conn: sqlite3.Connection, path: Path, rel: str) -> bool:
        stat = path.stat()
        existing = conn.execute(
            "select size, mtime_ns, sha256, language, line_count from files where path = ?",
            (rel,),
        ).fetchone()
        if existing and int(existing[0]) == int(stat.st_size) and int(existing[1]) == int(stat.st_mtime_ns):
            return False

        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if existing and existing[2] == digest:
            conn.execute(
                """
                update files
                set size = ?, mtime_ns = ?, indexed_at = ?
                where path = ?
                """,
                (int(stat.st_size), int(stat.st_mtime_ns), _utc_now(), rel),
            )
            return False
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        conn.execute("delete from chunks where path = ?", (rel,))
        chunks = _build_chunks(rel, text)
        for start, end, kind, chunk_text in chunks:
            conn.execute(
                "insert into chunks(path, start_line, end_line, kind, text, vector_json) values(?, ?, ?, ?, ?, ?)",
                (rel, start, end, kind, chunk_text, json.dumps(_embed(f"{rel}\n{chunk_text}"), separators=(",", ":"))),
            )
        conn.execute(
            """
            insert into files(path, size, mtime_ns, sha256, language, line_count, indexed_at)
            values(?, ?, ?, ?, ?, ?, ?)
            on conflict(path) do update set
                size = excluded.size,
                mtime_ns = excluded.mtime_ns,
                sha256 = excluded.sha256,
                language = excluded.language,
                line_count = excluded.line_count,
                indexed_at = excluded.indexed_at
            """,
            (rel, int(stat.st_size), int(stat.st_mtime_ns), digest, path.suffix.lower().lstrip("."), len(lines), _utc_now()),
        )
        return True

    def _rel(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.root).as_posix()

    def _emit(self, progress: Callable[[ScanStats], None] | None, stats: ScanStats) -> None:
        if progress is not None:
            progress(stats)

    def _workspace_scan_lock(self) -> threading.Lock:
        key = str(self.root).lower()
        with self._lock_guard:
            lock = self._scan_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._scan_locks[key] = lock
            return lock


def _build_chunks(path: str, text: str) -> list[tuple[int, int, str, str]]:
    lines = text.splitlines()
    chunks: list[tuple[int, int, str, str]] = []
    symbols = _extract_python_symbols(path, text)
    chunks.extend(symbols)
    if not lines:
        return chunks
    step = max(1, _CHUNK_LINES - _CHUNK_OVERLAP)
    for start_idx in range(0, len(lines), step):
        end_idx = min(len(lines), start_idx + _CHUNK_LINES)
        chunk_text = "\n".join(lines[start_idx:end_idx]).strip()
        if chunk_text:
            chunks.append((start_idx + 1, end_idx, "chunk", chunk_text))
        if end_idx >= len(lines):
            break
    return chunks


def _extract_python_symbols(path: str, text: str) -> list[tuple[int, int, str, str]]:
    if not path.endswith(".py"):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    chunks: list[tuple[int, int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start)
        preview = "\n".join(lines[start - 1:min(end, start + 80)])
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        chunks.append((start, min(end, start + 80), kind, preview))
    return chunks


def _score_hit(
    query: str,
    q_terms: list[str],
    q_vec: list[float],
    path: str,
    text: str,
    vector_json: str,
) -> tuple[float, list[str]]:
    text_l = text.lower()
    path_l = path.lower()
    score = 0.0
    reasons: list[str] = []
    query_l = query.lower()
    if query_l and query_l in text_l:
        score += 8
        reasons.append("exact_text")
    if query_l and query_l in path_l:
        score += 6
        reasons.append("path")
    term_hits = 0
    for term in q_terms:
        if term in path_l:
            score += 4
            term_hits += 1
            reasons.append(f"path:{term}")
        if re.search(rf"\b{re.escape(term)}\b", text_l):
            score += 3
            term_hits += 1
            reasons.append(f"text:{term}")
        elif len(term) >= 4 and re.search(".*?".join(re.escape(ch) for ch in term), text_l):
            score += 0.8
            reasons.append(f"fuzzy:{term}")
    if term_hits >= 2:
        score += 2.5
        reasons.append("term_cooccurrence")
    try:
        chunk_vec = json.loads(vector_json)
        semantic = _cosine(q_vec, chunk_vec)
    except Exception:
        semantic = 0.0
    if semantic > 0.18:
        score += semantic * 8
        reasons.append("semantic")
    return score, list(dict.fromkeys(reasons))


def _embed(text: str) -> list[float]:
    vector = [0.0] * _VECTOR_DIMS
    for token in _tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        idx = value % _VECTOR_DIMS
        sign = 1.0 if (value >> 8) & 1 else -1.0
        vector[idx] += sign
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [round(v / norm, 6) for v in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    return sum(left[i] * right[i] for i in range(size))


def _tokens(text: str) -> list[str]:
    pieces = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[A-Za-z0-9./-]+", text.lower())
    stop = {"the", "and", "for", "from", "that", "this", "with", "what", "where", "when", "into", "will", "have"}
    return [piece for piece in pieces if len(piece) >= 2 and piece not in stop]


def _snippet(text: str, terms: list[str], *, max_chars: int = 700) -> str:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        lower = line.lower()
        if any(term in lower for term in terms):
            start = max(0, idx - 2)
            end = min(len(lines), idx + 3)
            return _compact("\n".join(lines[start:end]), max_chars)
    return _compact(text, max_chars)


def _compact(text: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 3)].rstrip() + "..."


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _looks_generated_dir(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(".egg-info") or lower in {"vendor", "vendors"}


def _is_ignored_dir(name: str) -> bool:
    if name in _IGNORE_DIRS or _looks_generated_dir(name):
        return True
    return name.startswith(".") and name not in {".github"}
