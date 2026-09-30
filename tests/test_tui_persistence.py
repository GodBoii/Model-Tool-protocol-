from __future__ import annotations

import threading
import time
from typing import Any

from mtp.cli.tui_persistence import SessionSaver
from mtp.cli.tui_workers import SessionSnapshot


def _snap(session_id: str, marker: int) -> SessionSnapshot:
    return SessionSnapshot(store=None, session_id=session_id, user_id=None, tui_metadata={"marker": marker})


def test_rapid_requests_coalesce_into_one_write() -> None:
    writes: list[SessionSnapshot] = []
    saver = SessionSaver(write=writes.append, delay=0.05)
    for marker in range(10):
        saver.request(_snap("s1", marker))
    time.sleep(0.3)
    assert [w.tui_metadata["marker"] for w in writes] == [9]
    saver.close()


def test_flush_writes_pending_immediately() -> None:
    writes: list[SessionSnapshot] = []
    saver = SessionSaver(write=writes.append, delay=60)
    saver.request(_snap("s1", 1))
    saver.flush()
    assert len(writes) == 1
    saver.close()


def test_latest_snapshot_is_kept_per_session() -> None:
    writes: list[SessionSnapshot] = []
    saver = SessionSaver(write=writes.append, delay=60)
    saver.request(_snap("old", 1))
    saver.request(_snap("new", 2))
    saver.request(_snap("old", 3))
    saver.close()
    assert sorted((w.session_id, w.tui_metadata["marker"]) for w in writes) == [("new", 2), ("old", 3)]


def test_write_errors_are_reported() -> None:
    errors: list[BaseException] = []

    def failing(_: SessionSnapshot) -> None:
        raise OSError("disk full")

    saver = SessionSaver(write=failing, on_error=errors.append, delay=60)
    saver.request(_snap("s1", 1))
    saver.close()
    assert [str(e) for e in errors] == ["disk full"]


def test_tasks_and_writes_run_serially_in_order() -> None:
    order: list[str] = []
    active = threading.Lock()

    def write(snapshot: SessionSnapshot) -> None:
        assert active.acquire(blocking=False), "writes overlapped"
        order.append(f"write:{snapshot.session_id}")
        active.release()

    saver = SessionSaver(write=write, delay=60)
    saver.request(_snap("s1", 1))
    saver.flush()
    saver.run_task(lambda: order.append("task"))
    saver.close()
    assert order == ["write:s1", "task"]


def test_requests_after_close_are_ignored() -> None:
    writes: list[Any] = []
    saver = SessionSaver(write=writes.append, delay=0.01)
    saver.close()
    saver.request(_snap("s1", 1))
    saver.close()
    time.sleep(0.05)
    assert writes == []
