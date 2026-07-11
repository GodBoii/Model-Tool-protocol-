from __future__ import annotations

import json

from mtp.cli.tui_settings import mask_api_key, save_provider_settings


def test_mask_api_key_never_returns_full_secret() -> None:
    assert mask_api_key("short") == "*****"
    assert mask_api_key("sk-1234567890-secret") == "sk-1...cret"
    assert mask_api_key("") == ""


def test_save_provider_settings_replaces_file_and_cleans_temporary_file(tmp_path) -> None:
    path = tmp_path / "tui_provider_settings.json"
    path.write_text("old", encoding="utf-8")

    save_provider_settings(path, {"providers": {"groq": {"api_key": "secret"}}})

    assert json.loads(path.read_text(encoding="utf-8"))["providers"]["groq"]["api_key"] == "secret"
    assert list(tmp_path.glob("*.tmp")) == []
