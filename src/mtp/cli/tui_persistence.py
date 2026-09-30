"""Debounced, off-thread session persistence for the TUI.

``save_tui_session`` rewrites the whole sessions.json file. The TUI used to
call it on the UI thread after almost every command and twice per turn.
``SessionSaver`` takes a snapshot on the UI thread (cheap, no I/O), keeps
only the newest snapshot per session, and writes after a short quiet period
on a single background thread so writes never overlap.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .tui_workers import SessionSnapshot, write_session_snapshot

DEFAULT_DELAY = 0.5


class SessionSaver:
    def __init__(
        self,
        *,
        write: Callable[[SessionSnapshot], None] = write_session_snapshot,
        on_error: Callable[[BaseException], None] | None = None,
        delay: float = DEFAULT_DELAY,
    ) -> None:
        self._write = write
        self._on_error = on_error
        self._delay = delay
        self._lock = threading.Lock()
        self._pending: dict[str, SessionSnapshot] = {}
        self._timer: threading.Timer | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mtp-session-save")
        self._closed = False

    def request(self, snapshot: SessionSnapshot) -> None:
        """Queue ``snapshot``; it replaces any unsaved snapshot of the same session."""
        with self._lock:
            if self._closed:
                return
            self._pending[snapshot.session_id] = snapshot
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._schedule_drain)
            self._timer.daemon = True
            self._timer.start()

    def run_task(self, task: Callable[[], None]) -> None:
        """Run other persistence work (e.g. memory summaries) on the writer thread."""
        with self._lock:
            if self._closed:
                return
            self._executor.submit(self._guarded, task)

    def flush(self) -> None:
        """Write everything pending and wait for queued work to finish."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._closed:
                return
            future = self._executor.submit(self._drain)
        future.result()

    def close(self) -> None:
        """Flush and stop. Safe to call more than once."""
        self.flush()
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True)

    def _schedule_drain(self) -> None:
        with self._lock:
            self._timer = None
            if self._closed:
                return
            self._executor.submit(self._drain)

    def _drain(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
        for snapshot in pending.values():
            self._guarded(lambda snapshot=snapshot: self._write(snapshot))

    def _guarded(self, task: Callable[[], None]) -> None:
        try:
            task()
        except Exception as exc:  # reported, not swallowed: the UI shows it
            if self._on_error is not None:
                self._on_error(exc)

