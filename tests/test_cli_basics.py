from __future__ import annotations

import json
import pytest

from mtp.session_store import JsonSessionStore, SessionRecord

from mtp.cli.main import main
from mtp.cli.doctor import DoctorItem
from mtp.cli.providers import ProviderInfo


def test_version_prints_package_version(capsys) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert capsys.readouterr().out.startswith("mtp ")


def test_doctor_warnings_do_not_fail(monkeypatch) -> None:
    monkeypatch.setattr(
        "mtp.cli.main.run_doctor",
        lambda provider_filter=None: [DoctorItem("optional", "WARN", "not installed")],
    )

    assert main(["doctor"]) == 0


def test_tui_rejects_unbounded_round_count(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["tui", "--max-rounds", "1000000"])
    assert exc_info.value.code == 2
    assert "1 to 100" in capsys.readouterr().err


def test_doctor_failure_returns_nonzero(monkeypatch) -> None:
    monkeypatch.setattr(
        "mtp.cli.main.run_doctor",
        lambda provider_filter=None: [DoctorItem("python", "FAIL", "too old")],
    )

    assert main(["doctor"]) == 1


def test_doctor_json_is_machine_readable(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "mtp.cli.main.run_doctor",
        lambda provider_filter=None: [DoctorItem("python", "OK", "Python 3")],
    )

    assert main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"check": "python", "status": "OK", "detail": "Python 3"}
    ]


def test_providers_list_json_is_machine_readable(capsys) -> None:
    assert main(["providers", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert any(row["name"] == "groq" for row in payload)
    assert all("key_status" in row and "ready" in row for row in payload)


def test_dotted_optional_sdk_probe_handles_missing_parent(monkeypatch) -> None:
    def missing_parent(_module: str):
        raise ModuleNotFoundError("No module named 'google'")

    monkeypatch.setattr("mtp.cli.providers.importlib.util.find_spec", missing_parent)

    assert ProviderInfo("gemini", "Gemini", "GeminiProvider", "google.genai", None).sdk_installed() is False


def test_providers_show_reports_readiness_without_secret(monkeypatch, capsys) -> None:
    secret = "test-secret-that-must-not-be-printed"
    monkeypatch.setenv("GROQ_API_KEY", secret)
    monkeypatch.setattr("mtp.cli.providers.importlib.util.find_spec", lambda _module: object())
    monkeypatch.setattr(
        "mtp.cli.providers.provider_credential_status",
        lambda *_args, **_kwargs: (True, "environment"),
    )

    assert main(["providers", "show", "Groq", "--json"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["name"] == "groq"
    assert payload["sdk_status"] == "installed"
    assert payload["key_status"] == "configured"
    assert payload["key_source"] == "environment"
    assert payload["ready"] is True
    assert secret not in output


def test_providers_show_reports_missing_requirements(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("mtp.cli.providers.importlib.util.find_spec", lambda _module: None)
    monkeypatch.setattr("mtp.cli.providers.provider_credential_status", lambda *_args, **_kwargs: (False, None))

    assert main(["providers", "show", "OpenAIToolCallingProvider", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sdk_status"] == "missing"
    assert payload["key_status"] == "missing"
    assert payload["ready"] is False


def test_providers_show_rejects_unknown_provider(capsys) -> None:
    assert main(["providers", "show", "not-a-provider"]) == 2
    assert "Unknown provider: not-a-provider" in capsys.readouterr().err


def test_sessions_list_and_delete(tmp_path, capsys) -> None:
    JsonSessionStore(db_path=tmp_path).upsert_session(
        SessionRecord(session_id="session-1", user_id="user-1", messages=[{"role": "user", "content": "hi"}])
    )

    assert main(["sessions", "list", "--session-db", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["session_id"] == "session-1"

    assert main(["sessions", "delete", "session-1", "--session-db", str(tmp_path)]) == 2
    assert main([
        "sessions", "delete", "session-1", "--session-db", str(tmp_path),
        "--user-id", "user-1", "--yes",
    ]) == 0
    assert JsonSessionStore(db_path=tmp_path).get_session("session-1") is None
