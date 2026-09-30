"""Coalesce live run events on the worker thread before they reach the UI.

Providers emit one event per token. Sending each one with
``App.call_from_thread`` blocks the worker until the UI handles it, so a
busy UI slows the model stream down. ``LiveEventBatcher`` merges adjacent
text or reasoning chunks and hands batches to a non-blocking sink at most
once per interval. Other events (tools, status, warnings) flush right away
so tool rows still appear immediately. Order is preserved.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

LiveEvent = tuple[str, Any]

# Event kinds whose payloads are string chunks that can be concatenated.
COALESCED_KINDS = frozenset({"text", "reasoning"})
DEFAULT_INTERVAL = 0.03


class LiveEventBatcher:
    """Thread-safe buffer that forwards ``(kind, message)`` events in batches.

    ``sink`` must not block; it is called with the buffer lock held so that
    batches from the flusher thread and the producer thread stay in order.
    """

    def __init__(self, sink: Callable[[list[LiveEvent]], None], *, interval: float = DEFAULT_INTERVAL) -> None:
        self._sink = sink
        self._interval = interval
        self._lock = threading.Lock()
        self._buffer: list[LiveEvent] = []
        self._closed = threading.Event()
        self._flusher: threading.Thread | None = None

    def emit(self, kind: str, message: Any) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            if kind in COALESCED_KINDS and isinstance(message, str):
                last = self._buffer[-1] if self._buffer else None
                if last is not None and last[0] == kind and isinstance(last[1], str):
                    self._buffer[-1] = (kind, last[1] + message)
                else:
                    self._buffer.append((kind, message))
                self._ensure_flusher()
                return
            self._buffer.append((kind, message))
            self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def close(self) -> None:
        """Flush what is buffered and stop accepting events."""
        with self._lock:
            self._flush_locked()
            self._closed.set()
        if self._flusher is not None and self._flusher is not threading.current_thread():
            self._flusher.join(timeout=1)

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        self._sink(batch)

    def _ensure_flusher(self) -> None:
        if self._flusher is None:
            self._flusher = threading.Thread(target=self._run_flusher, name="mtp-live-events", daemon=True)
            self._flusher.start()

    def _run_flusher(self) -> None:
        while not self._closed.wait(self._interval):
            self.flush()
