from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from mtp import _subprocess as process_utils
from mtp._subprocess import SubprocessCancelledError, run_subprocess
from mtp.toolkits.python_toolkit import PythonToolkit


def test_run_subprocess_timeout_reaps_child(tmp_path) -> None:
    pid_file = tmp_path / "pid.txt"
    code = (
        "import os,time,pathlib; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess([sys.executable, "-c", code], timeout=0.3)

    pid = int(pid_file.read_text())
    with pytest.raises(OSError):
        os.kill(pid, 0)


def test_python_toolkit_honors_runtime_cancel_event(tmp_path) -> None:
    handler = PythonToolkit(base_dir=tmp_path, timeout_seconds=30).load_tools()[0].handler
    cancelled = threading.Event()
    timer = threading.Timer(0.15, cancelled.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(SubprocessCancelledError):
            handler(code="import time; time.sleep(30)", cancel_event=cancelled)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 3


def test_posix_cleanup_escalates_from_term_to_kill(monkeypatch) -> None:
    calls: list[int] = []

    class Process:
        pid = 4321
        def poll(self):
            return None
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("fake", timeout)

    monkeypatch.setattr(process_utils.os, "name", "posix")
    monkeypatch.setattr(process_utils.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(
        process_utils.os, "killpg", lambda _pid, sig: calls.append(sig), raising=False
    )
    process_utils.terminate_process_tree(
        Process(), terminate_grace_seconds=0, kill_grace_seconds=0
    )
    assert calls == [signal.SIGTERM, 9]


def test_pre_cancelled_process_is_cleaned_up() -> None:
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(SubprocessCancelledError):
        run_subprocess([sys.executable, "-c", "import time; time.sleep(30)"], cancel_event=cancelled)
