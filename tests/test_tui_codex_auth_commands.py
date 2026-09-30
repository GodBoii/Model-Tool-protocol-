from __future__ import annotations

import subprocess
from pathlib import Path
import base64
import json

from mtp.cli.tui_commands import parse_slash_command
from mtp.cli import tui_codex_backend


def test_parse_codex_namespaced_auth_commands() -> None:
    assert parse_slash_command("/codex login") == ("codex", "login")
    assert parse_slash_command("/codex logout") == ("codex", "logout")
    assert parse_slash_command("/codex status") == ("codex", "status")
    assert parse_slash_command("/codex account") == ("codex", "account")
    assert parse_slash_command("/codex doctor") == ("codex", "doctor")
    assert parse_slash_command("/codex repair-config") == ("codex", "repair-config")
    assert parse_slash_command("/codex login --device-auth") == ("codex", "login --device-auth")


def test_parse_legacy_codex_login_alias() -> None:
    assert parse_slash_command("/codex-login") == ("codex", "login")


def test_codex_exec_command_uses_supported_fresh_sandbox_flag(tmp_path: Path) -> None:
    cmd = tui_codex_backend._build_codex_exec_command(
        codex_bin="codex",
        cwd=tmp_path,
        output_path=tmp_path / "out.txt",
        prompt="hello",
        model="gpt-5.5",
        reasoning_effort="medium",
        session_id=None,
        sandbox_mode="workspace-write",
    )

    assert "--sandbox" in cmd
    assert "workspace-write" in cmd


def test_codex_exec_resume_uses_config_sandbox_override(tmp_path: Path) -> None:
    cmd = tui_codex_backend._build_codex_exec_command(
        codex_bin="codex",
        cwd=tmp_path,
        output_path=tmp_path / "out.txt",
        prompt="hello",
        model="gpt-5.5",
        reasoning_effort="medium",
        session_id="session-123",
        sandbox_mode="workspace-write",
    )

    assert "--sandbox" not in cmd
    assert "-c" in cmd
    assert 'sandbox_mode="workspace-write"' in cmd


def test_codex_login_delegates_to_official_cli(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = tui_codex_backend.run_codex_login("codex", ["--device-auth"])

    assert result.return_code == 0
    assert calls == [["codex", "login", "--device-auth"]]


def test_codex_logout_delegates_to_official_cli(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = tui_codex_backend.run_codex_logout("codex")

    assert result.return_code == 0
    assert calls == [["codex", "logout"]]


def test_codex_status_delegates_to_login_status(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="Logged in\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = tui_codex_backend.run_codex_login_status("codex")

    assert result.return_code == 0
    assert result.output == "Logged in"
    assert calls == [["codex", "login", "status"]]


def test_codex_doctor_can_capture_when_requested(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="Doctor OK\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = tui_codex_backend.run_codex_doctor("codex", capture=True)

    assert result.return_code == 0
    assert result.output == "Doctor OK"
    assert calls == [["codex", "doctor"]]


def _fake_jwt(claims: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def enc(payload: dict) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{enc(header)}.{enc(claims)}.signature"


def test_account_summary_extracts_safe_profile_without_tokens(tmp_path: Path) -> None:
    id_token = _fake_jwt({
        "email": "person@example.com",
        "name": "Person Example",
        "exp": 1781630353,
    })
    access_token = _fake_jwt({
        "https://api.openai.com/profile": {"email": "person@example.com", "email_verified": True},
        "exp": 1782490753,
    })
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({
            "auth_mode": "chatgpt",
            "tokens": {
                "id_token": id_token,
                "access_token": access_token,
                "refresh_token": "refresh-secret",
                "account_id": "acct_1234567890",
            },
            "last_refresh": "2026-06-16T10:00:00Z",
        }),
        encoding="utf-8",
    )
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5.5"\nmodel_reasoning_effort = "medium"\n[windows]\nsandbox = "unelevated"\n', encoding="utf-8")

    summary = tui_codex_backend.build_codex_account_summary(
        codex_bin="codex",
        auth_path=auth,
        config_path=config,
        last_usage_lines=["tokens(in/out/total/reasoning)=1/2/3/0", "rate_remaining=42%"],
        last_warnings=["You've hit your usage limit. Try again tomorrow."],
    )

    assert "email=person@example.com" in summary
    assert "name=Person Example" in summary
    assert "auth_mode=chatgpt" in summary
    assert "model=gpt-5.5" in summary
    assert "windows.sandbox=unelevated" in summary
    assert "rate_remaining=42%" in summary
    assert "Recent limit warnings:" in summary
    assert "usage limit" in summary
    assert id_token not in summary
    assert access_token not in summary
    assert "refresh-secret" not in summary


def test_detects_and_repairs_invalid_service_tier(tmp_path) -> None:
    output = "unknown variant `default`, expected `fast` or `flex`"
    issue = tui_codex_backend.detect_codex_config_issue(f"service_tier: {output}")
    assert issue is tui_codex_backend.CodexConfigIssue.INVALID_SERVICE_TIER_DEFAULT

    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5.5"\nservice_tier = "default"\n', encoding="utf-8")

    message = tui_codex_backend.repair_codex_config_issue(issue, config_path=config)

    assert "commented out active service_tier" in message
    assert '# service_tier = "default"' in config.read_text(encoding="utf-8")
    assert (tmp_path / "config.toml.bak").exists()


def test_detects_unsupported_service_tier() -> None:
    issue = tui_codex_backend.detect_codex_config_issue("Unsupported service_tier: flex")
    assert issue is tui_codex_backend.CodexConfigIssue.UNSUPPORTED_SERVICE_TIER


def test_usage_limit_hint_does_not_suggest_login() -> None:
    hint = tui_codex_backend._codex_failure_hint("You've hit your usage limit. Try again tomorrow.")

    assert "usage limit" in hint
    assert "/codex login" not in hint
