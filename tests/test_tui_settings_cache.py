from __future__ import annotations

import json
import os
from pathlib import Path

from mtp.cli import tui_settings
from mtp.cli.tui_settings import ensure_provider_entry, load_provider_settings, save_provider_settings


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_repeated_loads_parse_once(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tui_provider_settings.json"
    _write(path, {"providers": {"groq": {"model": "m1"}}})
    calls = 0
    real_parse = tui_settings._parse_settings

    def counting(raw: str) -> dict:
        nonlocal calls
        calls += 1
        return real_parse(raw)

    monkeypatch.setattr(tui_settings, "_parse_settings", counting)
    tui_settings._SETTINGS_CACHE.clear()
    for _ in range(5):
        assert load_provider_settings(path)["providers"]["groq"]["model"] == "m1"
    assert calls == 1


def test_callers_get_independent_copies(tmp_path: Path) -> None:
    path = tmp_path / "tui_provider_settings.json"
    _write(path, {"providers": {}})
    first = load_provider_settings(path)
    ensure_provider_entry(first, "groq")["api_key"] = "unsaved"
    second = load_provider_settings(path)
    assert "groq" not in second["providers"]


def test_external_edit_is_picked_up(tmp_path: Path) -> None:
    path = tmp_path / "tui_provider_settings.json"
    _write(path, {"providers": {"groq": {"model": "old"}}})
    assert load_provider_settings(path)["providers"]["groq"]["model"] == "old"
    _write(path, {"providers": {"groq": {"model": "newer-and-longer"}}})
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert load_provider_settings(path)["providers"]["groq"]["model"] == "newer-and-longer"


def test_save_is_atomic_and_updates_cache(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "tui_provider_settings.json"
    save_provider_settings(path, {"providers": {"groq": {"model": "saved"}}})
    assert json.loads(path.read_text(encoding="utf-8"))["providers"]["groq"]["model"] == "saved"
    assert load_provider_settings(path)["providers"]["groq"]["model"] == "saved"
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_missing_file_returns_default(tmp_path: Path) -> None:
    assert load_provider_settings(tmp_path / "absent.json") == {"providers": {}}


def test_corrupt_file_is_backed_up_once_and_reported(tmp_path: Path) -> None:
    from mtp.cli.tui_settings import pop_settings_recoveries

    tui_settings._SETTINGS_CACHE.clear()
    path = tmp_path / "tui_provider_settings.json"
    original = '{"providers": {"groq": {"api_key": "sk-keep-me"'  # truncated JSON
    path.write_text(original, encoding="utf-8")

    assert load_provider_settings(path) == {"providers": {}}
    assert load_provider_settings(path) == {"providers": {}}
    recoveries = pop_settings_recoveries()
    assert len(recoveries) == 1
    backup = recoveries[0].backup_path
    assert backup is not None and backup.read_text(encoding="utf-8") == original
    assert "invalid JSON" in recoveries[0].message()
    assert pop_settings_recoveries() == []

    # Saving afterwards writes a fresh file; the backup keeps the old content.
    save_provider_settings(path, {"providers": {"groq": {"model": "m"}}})
    assert backup.read_text(encoding="utf-8") == original
    assert len(list(tmp_path.glob("*.corrupt-*"))) == 1


def test_wrong_shape_is_treated_as_corrupt(tmp_path: Path) -> None:
    from mtp.cli.tui_settings import pop_settings_recoveries

    tui_settings._SETTINGS_CACHE.clear()
    path = tmp_path / "tui_provider_settings.json"
    _write(path, {"providers": ["not", "a", "dict"]})
    assert load_provider_settings(path) == {"providers": {}}
    [recovery] = pop_settings_recoveries()
    assert "'providers' is not an object" in recovery.reason


def test_missing_providers_key_is_filled_in(tmp_path: Path) -> None:
    from mtp.cli.tui_settings import pop_settings_recoveries

    tui_settings._SETTINGS_CACHE.clear()
    path = tmp_path / "tui_provider_settings.json"
    _write(path, {"other": 1})
    assert load_provider_settings(path) == {"other": 1, "providers": {}}
    assert pop_settings_recoveries() == []
