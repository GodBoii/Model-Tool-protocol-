from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

from mtp.cli import tui_codex_backend as codex_backend
from mtp.cli.tui_codex_backend import CODEX_CANCELLED_TEXT, CodexRunHandle, _run_codex_command

# Parent prints a line, then starts a grandchild that inherits stdout and
# sleeps. This mirrors codex.cmd -> node on Windows: killing only the parent
# would leave the pipe open and the reader blocked.
_PARENT_WITH_GRANDCHILD = (
    "import subprocess, sys, time\n"
    "print('started', flush=True)\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "time.sleep(60)\n"
)


def test_cancel_kills_process_tree_and_unblocks_reader() -> None:
    handle = CodexRunHandle()
    lines: list[str] = []

    def emit(payload: str) -> None:
        lines.append(payload)

    result: dict[str, Any] = {}

    def run() -> None:
        result["value"] = _run_codex_command(
            cmd=[sys.executable, "-c", _PARENT_WITH_GRANDCHILD], emit=emit, handle=handle
        )

    worker = threading.Thread(target=run)
    started_at = time.monotonic()
    worker.start()
    # _emit_codex_live_line ignores non-JSON lines, so poll the handle instead.
    while handle._proc is None and time.monotonic() - started_at < 10:
        time.sleep(0.05)
    time.sleep(0.5)  # let the grandchild start

    assert handle.cancel() is True
    assert handle.cancel() is False
    worker.join(timeout=15)

    assert not worker.is_alive(), "reader stayed blocked after cancel"
    assert time.monotonic() - started_at < 15
    return_code, stdout_text = result["value"]
    assert return_code != 0
    assert "started" in stdout_text


def test_cancel_before_start_kills_on_attach() -> None:
    handle = CodexRunHandle()
    handle.cancel()
    started_at = time.monotonic()
    return_code, _ = _run_codex_command(
        cmd=[sys.executable, "-c", "import time; time.sleep(60)"], emit=None, handle=handle
    )
    assert return_code != 0
    assert time.monotonic() - started_at < 15


def test_cancelled_resume_is_not_retried(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    handle = CodexRunHandle()

    def fake_run(*, cmd: list[str], emit: Any, handle: CodexRunHandle | None = None) -> tuple[int, str]:
        calls.append(cmd)
        assert handle is not None
        handle.cancel()
        return 1, ""

    monkeypatch.setattr(codex_backend, "_run_codex_command", fake_run)
    result = codex_backend.run_codex_prompt(
        codex_bin="codex",
        cwd=tmp_path,
        prompt="hi",
        model="gpt-test",
        reasoning_effort="medium",
        previous_session_id="thread-123",
        conversation_history=[("q", "a")],
        handle=handle,
    )
    assert len(calls) == 1
    assert result.text == CODEX_CANCELLED_TEXT
