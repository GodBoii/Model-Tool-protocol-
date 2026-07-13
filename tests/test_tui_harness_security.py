from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from mtp._subprocess import SubprocessCancelledError
from mtp.policy import PolicyDecision
from mtp.protocol import ToolCall
from mtp.cli.tui_harness_policy import (
    HarnessRiskPolicy,
    normalize_sandbox_mode,
    permissions_for_sandbox,
)
from mtp.cli.tui_harness_tools import CommandToolkit, EditToolkit


def _decision(profile: str, tool_name: str, arguments: dict[str, object]) -> PolicyDecision:
    tool = next(
        spec
        for loader in (CommandToolkit("."), EditToolkit("."))
        for spec in loader.list_tool_specs()
        if spec.name == tool_name
    )
    call = ToolCall(id="call-1", name=tool_name, arguments=arguments)
    return HarnessRiskPolicy(
        mode="code", permissions=permissions_for_sandbox(profile)
    ).decide(tool, call, arguments)


def test_workspace_write_does_not_silently_approve_ask_operations() -> None:
    assert _decision("workspace-write", "edit.create_file", {"path": "new.py"}) is PolicyDecision.ASK
    assert _decision("workspace-write", "shell.run", {"command": "echo changed > file"}) is PolicyDecision.ASK
    assert _decision("workspace-write", "test.run", {"command": "pytest -q"}) is PolicyDecision.ASK
    assert _decision("workspace-write", "shell.run", {"command": "git status --short"}) is PolicyDecision.ALLOW


def test_read_only_is_an_explicit_deny_boundary() -> None:
    assert _decision("read-only", "edit.create_file", {"path": "new.py"}) is PolicyDecision.DENY
    assert _decision("read-only", "shell.run", {"command": "git status"}) is PolicyDecision.DENY
    assert _decision("read-only", "test.run", {"command": "pytest -q"}) is PolicyDecision.DENY


def test_explicit_denies_survive_full_access_profile() -> None:
    assert _decision("danger-full-access", "shell.run", {"command": "rm important.txt"}) is PolicyDecision.DENY
    assert _decision("danger-full-access", "edit.create_file", {"path": "new.py"}) is PolicyDecision.ALLOW


@pytest.mark.parametrize("mode", ["", "writeable", "full-access", "READ ONLY"])
def test_invalid_sandbox_modes_are_rejected(mode: str) -> None:
    with pytest.raises(ValueError, match="Unknown sandbox mode"):
        normalize_sandbox_mode(mode)


def test_edit_tools_reject_workspace_escape(tmp_path: Path) -> None:
    handler = next(
        tool.handler for tool in EditToolkit(tmp_path).load_tools()
        if tool.spec.name == "edit.create_file"
    )
    outside_name = f"{tmp_path.name}-outside.txt"
    with pytest.raises(ValueError, match="escapes workspace"):
        handler(path=f"../{outside_name}", content="no")
    assert not (tmp_path.parent / outside_name).exists()


def test_shell_tool_cancellation_terminates_promptly(tmp_path: Path) -> None:
    handler = next(
        tool.handler for tool in CommandToolkit(tmp_path).load_tools()
        if tool.spec.name == "shell.run"
    )
    command = subprocess.list2cmdline(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    cancelled = threading.Event()
    timer = threading.Timer(0.15, cancelled.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(SubprocessCancelledError):
            handler(command=command, timeout_seconds=30, cancel_event=cancelled)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 3


def test_test_tool_forwards_runtime_cancellation(monkeypatch, tmp_path: Path) -> None:
    observed: dict[str, object] = {}

    def fake_run_subprocess(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr("mtp.cli.tui_harness_tools.run_subprocess", fake_run_subprocess)
    handler = next(
        tool.handler for tool in CommandToolkit(tmp_path).load_tools()
        if tool.spec.name == "test.run"
    )
    cancelled = threading.Event()
    result = handler(command="pytest -q tests", cancel_event=cancelled)
    assert result["returncode"] == 0
    assert observed["cancel_event"] is cancelled
    assert observed["shell"] is True


def test_shell_tool_description_does_not_claim_containment(tmp_path: Path) -> None:
    spec = next(
        spec for spec in CommandToolkit(tmp_path).list_tool_specs()
        if spec.name == "shell.run"
    )
    assert "not OS or filesystem containment" in spec.description
