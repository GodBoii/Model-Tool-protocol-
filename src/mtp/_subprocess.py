"""Subprocess helpers with bounded, process-tree-aware cleanup.

The standard :func:`subprocess.run` kills only the immediate child after a
timeout.  Tools commonly launch grandchildren (shell scripts, Python code,
test runners), so that behavior can leave work running after an agent has
been cancelled.  This module starts each child in its own process group and
always escalates from termination to a forced kill.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from os import PathLike
from typing import Any, Sequence


class SubprocessCancelledError(RuntimeError):
    """Raised when a cooperative cancellation event stops a subprocess."""


def _popen_group_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _wait_quietly(process: subprocess.Popen[Any], timeout: float) -> bool:
    try:
        process.wait(timeout=max(0.0, timeout))
        return True
    except subprocess.TimeoutExpired:
        return False


def terminate_process_tree(
    process: subprocess.Popen[Any],
    *,
    terminate_grace_seconds: float = 0.75,
    kill_grace_seconds: float = 0.75,
) -> None:
    """Terminate *process* and descendants, escalating to a forced kill.

    The function is deliberately idempotent and best-effort: cleanup must not
    hide the timeout/cancellation exception that caused it.
    """
    if process.poll() is not None:
        return

    if os.name == "nt":
        # taskkill /T addresses the process tree.  Keep its output private and
        # fall back to Popen methods if it is unavailable or denied.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(0.1, terminate_grace_seconds),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            try:
                process.terminate()
            except OSError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                process.terminate()
            except OSError:
                pass

    if _wait_quietly(process, terminate_grace_seconds):
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(process.pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(0.1, kill_grace_seconds),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass
    _wait_quietly(process, kill_grace_seconds)


def run_subprocess(
    args: Sequence[str] | str,
    *,
    cwd: str | bytes | PathLike[str] | PathLike[bytes] | None = None,
    timeout: float | None = None,
    cancel_event: threading.Event | None = None,
    poll_interval: float = 0.05,
    terminate_grace_seconds: float = 0.75,
    shell: bool = False,
    text: bool = True,
    encoding: str | None = None,
    errors: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run a captured subprocess that cannot outlive cancellation/timeout."""
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be greater than zero or None")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be greater than zero")

    process = subprocess.Popen(
        args,
        cwd=cwd,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding=encoding,
        errors=errors,
        env=env,
        **_popen_group_options(),
    )
    started = time.monotonic()
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise SubprocessCancelledError("Subprocess execution was cancelled.")
            remaining = None if timeout is None else timeout - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)
            wait_for = poll_interval if remaining is None else min(poll_interval, remaining)
            try:
                stdout, stderr = process.communicate(timeout=wait_for)
                return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    except (SubprocessCancelledError, subprocess.TimeoutExpired):
        terminate_process_tree(
            process,
            terminate_grace_seconds=terminate_grace_seconds,
            kill_grace_seconds=terminate_grace_seconds,
        )
        # Reap the child and drain any pipe data after termination.
        try:
            process.communicate(timeout=max(0.1, terminate_grace_seconds))
        except (OSError, subprocess.SubprocessError):
            pass
        raise
    except BaseException:
        # KeyboardInterrupt and task-worker failures must not orphan children.
        terminate_process_tree(
            process,
            terminate_grace_seconds=terminate_grace_seconds,
            kill_grace_seconds=terminate_grace_seconds,
        )
        raise
