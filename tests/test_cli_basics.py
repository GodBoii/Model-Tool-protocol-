from __future__ import annotations

import json
from mtp.session_store import JsonSessionStore, SessionRecord

from mtp.cli.main import main
from mtp.cli.doctor import DoctorItem


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
