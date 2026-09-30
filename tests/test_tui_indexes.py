from __future__ import annotations

import json
import threading
from pathlib import Path

from mtp.cli.tui_indexes import (
    BackgroundIndex,
    file_signature,
    load_session_summaries,
    scan_workspace_files,
)


def _wait_ready() -> tuple[threading.Event, callable]:
    event = threading.Event()
    return event, event.set


def test_index_is_built_off_thread_then_served_from_memory() -> None:
    calls: list[str] = []
    ready, on_ready = _wait_ready()
    index: BackgroundIndex[str, str] = BackgroundIndex(lambda key: calls.append(key) or f"v:{key}", on_ready=on_ready)
    assert index.get("a") is None
    assert ready.wait(2)
    assert index.get("a") == "v:a"
    assert index.get("a") == "v:a"
    assert calls == ["a"]


def test_new_key_does_not_return_old_value() -> None:
    ready, on_ready = _wait_ready()
    index: BackgroundIndex[str, str] = BackgroundIndex(lambda key: f"v:{key}", on_ready=on_ready)
    index.get("a")
    assert ready.wait(2)
    ready.clear()
    assert index.get("b") is None
    assert ready.wait(2)
    assert index.get("b") == "v:b"


def test_stale_value_is_served_while_rebuilding() -> None:
    now = [0.0]
    builds: list[int] = []
    ready, on_ready = _wait_ready()
    index: BackgroundIndex[str, int] = BackgroundIndex(
        lambda key: builds.append(1) or len(builds), on_ready=on_ready, max_age=10, clock=lambda: now[0],
    )
    index.get("k")
    assert ready.wait(2)
    ready.clear()
    now[0] = 11
    assert index.get("k") == 1
    assert ready.wait(2)
    assert index.get("k") == 2


def test_loader_failure_keeps_previous_value() -> None:
    fail = [False]
    ready, on_ready = _wait_ready()

    def loader(key: str) -> str:
        if fail[0]:
            raise OSError("boom")
        return "ok"

    now = [0.0]
    index: BackgroundIndex[str, str] = BackgroundIndex(loader, on_ready=on_ready, max_age=1, clock=lambda: now[0])
    index.get("k")
    assert ready.wait(2)
    fail[0] = True
    now[0] = 5
    assert index.get("k") == "ok"


def test_scan_matches_old_walk_rules(tmp_path: Path) -> None:
    for rel in ["a.py", "src/b.py", "src/pkg/c.py", "src/pkg/deep/d.py", ".hidden/x.py", "node_modules/y.js", ".dotfile"]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    files = scan_workspace_files(tmp_path)
    assert files.paths == ("a.py", "src/b.py", "src/pkg/c.py")
    assert files.matching("PKG") == ["src/pkg/c.py"]
    assert files.matching("py", limit=2) == ["a.py", "src/b.py"]


def test_session_summaries_are_sorted_newest_first(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps([
        {"session_id": "chat-aaaa1111", "updated_at": "2026-01-01", "metadata": {"tui": {"session_label": "old", "turn_count": 1}}},
        {"session_id": "chat-bbbb2222", "updated_at": "2026-02-01", "metadata": {"tui": {"session_label": "new", "turn_count": 3}}},
        "garbage",
    ]), encoding="utf-8")
    signature = file_signature(path)
    assert signature is not None
    summaries = load_session_summaries(signature)
    assert [(s.short_id, s.label, s.turns) for s in summaries] == [("bbbb2222", "new", 3), ("aaaa1111", "old", 1)]
    assert file_signature(tmp_path / "missing.json") is None
