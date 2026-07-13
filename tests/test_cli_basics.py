from __future__ import annotations

import json

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
