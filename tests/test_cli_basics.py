from __future__ import annotations

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
