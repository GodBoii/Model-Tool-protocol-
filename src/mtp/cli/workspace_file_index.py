"""Fast, bounded workspace file discovery for interactive completion."""
from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import time
from typing import Callable


DEFAULT_EXCLUDED_DIRS = frozenset(
    {"__pycache__", "node_modules", "venv", ".venv", ".git"}
)


class WorkspaceFileIndex:
    """A small TTL cache of workspace paths.

    Scans are deliberately bounded because :meth:`suggest` is called from the
    Textual UI thread.  An incomplete index is preferable to freezing input in
    a very large, slow, or network-backed workspace.
    """

    def __init__(
        self,
        *,
        cache_ttl: float = 15.0,
        max_files: int = 5_000,
        max_depth: int = 2,
        scan_time_budget: float = 0.025,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cache_ttl = max(0.0, cache_ttl)
        self.max_files = max(1, max_files)
        self.max_depth = max(0, max_depth)
        self.scan_time_budget = max(0.001, scan_time_budget)
        self._clock = clock
        self._root: Path | None = None
        self._files: tuple[str, ...] = ()
        self._refreshed_at = float("-inf")

    @property
    def files(self) -> tuple[str, ...]:
        return self._files

    def invalidate(self) -> None:
        self._root = None
        self._files = ()
        self._refreshed_at = float("-inf")

    def refresh(self, root: Path) -> tuple[str, ...]:
        root = root.expanduser().resolve()
        started = self._clock()
        found: list[str] = []
        pending: deque[tuple[Path, int]] = deque([(root, 0)])

        while pending and len(found) < self.max_files:
            if self._clock() - started >= self.scan_time_budget:
                break
            directory, depth = pending.popleft()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if self._clock() - started >= self.scan_time_budget:
                            pending.clear()
                            break
                        if entry.name.startswith("."):
                            continue
                        try:
                            if entry.is_file(follow_symlinks=False):
                                found.append(Path(entry.path).relative_to(root).as_posix())
                                if len(found) >= self.max_files:
                                    break
                            elif (
                                depth < self.max_depth
                                and entry.name not in DEFAULT_EXCLUDED_DIRS
                                and entry.is_dir(follow_symlinks=False)
                            ):
                                pending.append((Path(entry.path), depth + 1))
                        except OSError:
                            continue
            except (OSError, ValueError):
                continue

        self._root = root
        self._files = tuple(sorted(found, key=str.casefold))
        self._refreshed_at = self._clock()
        return self._files

    def suggest(self, root: Path, partial: str, *, limit: int = 20) -> list[str]:
        resolved_root = root.expanduser().resolve()
        if (
            resolved_root != self._root
            or self._clock() - self._refreshed_at >= self.cache_ttl
        ):
            self.refresh(resolved_root)

        needle = partial.casefold()
        return [path for path in self._files if needle in path.casefold()][:max(0, limit)]
