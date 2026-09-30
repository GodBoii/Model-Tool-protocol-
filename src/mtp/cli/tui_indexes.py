"""Background-built lookup data for input autocomplete.

``@file`` suggestions used to walk the workspace, and ``/load`` suggestions
used to parse sessions.json, on the UI thread for every keystroke. Here each
source is built once on a background thread, served from memory, and rebuilt
when its key changes or it gets too old.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
T = TypeVar("T")

# Matches the directories the old inline walk skipped.
_SKIP_DIRS = frozenset({"__pycache__", "node_modules", "venv", ".venv", ".git"})
MAX_INDEXED_FILES = 20_000
FILE_INDEX_MAX_AGE = 30.0


class BackgroundIndex(Generic[K, T]):
    """Caches ``loader(key)`` and rebuilds it on a daemon thread.

    ``get`` never blocks: it returns the cached value for ``key`` (possibly
    older than ``max_age``) or ``None``, and starts a rebuild when needed.
    ``on_ready`` runs on the loader thread after each successful build.
    """

    def __init__(
        self,
        loader: Callable[[K], T],
        *,
        on_ready: Callable[[], None] | None = None,
        max_age: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        name: str = "mtp-index",
    ) -> None:
        self._loader = loader
        self._on_ready = on_ready
        self._max_age = max_age
        self._clock = clock
        self._name = name
        self._lock = threading.Lock()
        self._key: K | None = None
        self._value: T | None = None
        self._loaded_at = 0.0
        self._loading_key: K | None = None
        self._has_value = False

    def get(self, key: K) -> T | None:
        with self._lock:
            matches = self._has_value and self._key == key
            fresh = matches and (self._max_age is None or self._clock() - self._loaded_at < self._max_age)
            if not fresh and self._loading_key != key:
                self._loading_key = key
                threading.Thread(target=self._load, args=(key,), name=self._name, daemon=True).start()
            return self._value if matches else None

    def invalidate(self) -> None:
        with self._lock:
            self._has_value = False
            self._key = None
            self._value = None

    def _load(self, key: K) -> None:
        try:
            value = self._loader(key)
        except Exception:
            # Keep serving the previous value; the next get() retries.
            with self._lock:
                if self._loading_key == key:
                    self._loading_key = None
            return
        with self._lock:
            if self._loading_key != key:
                return  # superseded by a newer key
            self._key = key
            self._value = value
            self._loaded_at = self._clock()
            self._has_value = True
            self._loading_key = None
        if self._on_ready is not None:
            self._on_ready()


@dataclass(frozen=True, slots=True)
class FileList:
    """Workspace-relative paths, sorted, with lowercase copies for matching."""

    paths: tuple[str, ...]
    lowered: tuple[str, ...]

    def matching(self, partial: str, limit: int = 20) -> list[str]:
        needle = partial.lower()
        found: list[str] = []
        for path, low in zip(self.paths, self.lowered):
            if needle in low:
                found.append(path)
                if len(found) >= limit:
                    break
        return found


def scan_workspace_files(cwd: Path, *, max_files: int = MAX_INDEXED_FILES) -> FileList:
    """List files up to two directories deep, skipping hidden and vendored dirs."""
    paths: list[str] = []
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
        rel_root = Path(root).relative_to(cwd)
        if rel_root == Path("."):
            rel_root = Path("")
        for name in files:
            if name.startswith("."):
                continue
            paths.append((rel_root / name).as_posix())
            if len(paths) >= max_files:
                break
        if len(paths) >= max_files:
            break
        if len(rel_root.parts) >= 2:
            dirs.clear()
    paths.sort()
    return FileList(paths=tuple(paths), lowered=tuple(p.lower() for p in paths))


@dataclass(frozen=True, slots=True)
class SessionSummary:
    short_id: str
    label: str
    turns: int
    updated_at: str

    def search_text(self) -> str:
        return f"{self.short_id} {self.label}".lower()


FileSignature = tuple[str, int, int]


def file_signature(path: Path) -> FileSignature | None:
    """Cheap change key for ``path``: one stat, no read."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


def load_session_summaries(signature: FileSignature, *, limit: int = 30) -> list[SessionSummary]:
    """Parse sessions.json into the newest ``limit`` summaries."""
    rows = json.loads(Path(signature[0]).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        return []
    summaries: list[SessionSummary] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata")
        tui = metadata.get("tui", {}) if isinstance(metadata, dict) else {}
        tui = tui if isinstance(tui, dict) else {}
        session_id = str(row.get("session_id") or "")
        summaries.append(SessionSummary(
            short_id=session_id.split("-")[-1][:8],
            label=str(tui.get("session_label") or ""),
            turns=int(tui.get("turn_count") or 0),
            updated_at=str(row.get("updated_at") or ""),
        ))
    summaries.sort(key=lambda item: item.updated_at, reverse=True)
    return summaries[:limit]
