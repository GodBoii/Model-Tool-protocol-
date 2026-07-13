from __future__ import annotations

import json

import pytest

from mtp.cli import tui_settings
from mtp.cli.tui_settings import (
    CredentialStorageError,
    delete_provider_api_key,
    ensure_provider_entry,
    load_provider_settings,
    mask_api_key,
    save_provider_settings,
    set_provider_api_key,
)


class _MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


@pytest.fixture
def memory_keyring(monkeypatch: pytest.MonkeyPatch) -> _MemoryKeyring:
    keyring = _MemoryKeyring()
    monkeypatch.setattr(tui_settings, "_keyring", lambda: keyring)
    return keyring


def test_mask_api_key_never_returns_full_secret() -> None:
    assert mask_api_key("short") == "*****"
    assert mask_api_key("sk-1234567890-secret") == "sk-1...cret"
    assert mask_api_key("") == ""


def test_save_provider_settings_secures_key_and_never_writes_it_to_json(
    tmp_path, memory_keyring: _MemoryKeyring
) -> None:
    path = tmp_path / "tui_provider_settings.json"
    path.write_text("old", encoding="utf-8")
    secret = "gsk_never_write_this_value"

    save_provider_settings(path, {"providers": {"groq": {"api_key": secret, "model": "test"}}})

    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "api_key" not in json.loads(raw)["providers"]["groq"]
    assert memory_keyring.values[(tui_settings.KEYRING_SERVICE, "groq")] == secret
    assert list(tmp_path.glob("*.tmp")) == []


def test_set_load_and_delete_round_trip_through_keyring(
    tmp_path, memory_keyring: _MemoryKeyring
) -> None:
    path = tmp_path / "tui_provider_settings.json"
    settings = {"providers": {}}

    set_provider_api_key(settings, "openai", "sk-vault-only")
    save_provider_settings(path, settings)
    loaded = load_provider_settings(path)

    assert ensure_provider_entry(loaded, "openai")["api_key"] == "sk-vault-only"
    assert delete_provider_api_key(loaded, "openai") is True
    save_provider_settings(path, loaded)
    assert memory_keyring.values == {}
    assert "sk-vault-only" not in path.read_text(encoding="utf-8")


def test_load_migrates_legacy_plaintext_only_after_successful_keyring_write(
    tmp_path, memory_keyring: _MemoryKeyring
) -> None:
    path = tmp_path / "tui_provider_settings.json"
    secret = "legacy-secret-value"
    path.write_text(json.dumps({"providers": {"groq": {"api_key": secret}}}), encoding="utf-8")

    loaded = load_provider_settings(path)

    assert ensure_provider_entry(loaded, "groq")["api_key"] == secret
    assert memory_keyring.values[(tui_settings.KEYRING_SERVICE, "groq")] == secret
    assert secret not in path.read_text(encoding="utf-8")
    assert "api_key" not in json.loads(path.read_text(encoding="utf-8"))["providers"]["groq"]


def test_failed_legacy_migration_does_not_destroy_only_copy(tmp_path, monkeypatch) -> None:
    path = tmp_path / "tui_provider_settings.json"
    secret = "legacy-must-not-be-lost"
    original = json.dumps({"providers": {"groq": {"api_key": secret}}})
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        tui_settings,
        "_store_keyring_api_key",
        lambda *_: (_ for _ in ()).throw(CredentialStorageError("use GROQ_API_KEY")),
    )

    with pytest.warns(RuntimeWarning, match="GROQ_API_KEY") as caught:
        loaded = load_provider_settings(path)

    assert ensure_provider_entry(loaded, "groq")["api_key"] == secret
    assert path.read_text(encoding="utf-8") == original
    assert secret not in str(caught[0].message)
    with pytest.raises(CredentialStorageError):
        save_provider_settings(path, loaded)
    assert path.read_text(encoding="utf-8") == original


def test_environment_is_read_only_fallback_and_is_never_persisted(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "tui_provider_settings.json"
    secret = "env-secret-value"
    monkeypatch.setenv("GROQ_API_KEY", secret)
    monkeypatch.setattr(tui_settings, "_read_keyring_api_key", lambda _provider: None)

    settings = load_provider_settings(path)
    entry = ensure_provider_entry(settings, "groq")
    save_provider_settings(path, settings)

    assert entry["api_key"] == secret
    assert entry["_api_key_source"] == "environment"
    assert delete_provider_api_key(settings, "groq") is False
    assert secret not in path.read_text(encoding="utf-8")


def test_unavailable_keyring_gives_actionable_error_and_writes_nothing(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "tui_provider_settings.json"
    monkeypatch.setattr(
        tui_settings,
        "_keyring",
        lambda: (_ for _ in ()).throw(CredentialStorageError("unavailable")),
    )

    with pytest.raises(CredentialStorageError) as caught:
        set_provider_api_key({"providers": {}}, "groq", "never-logged-secret")

    message = str(caught.value)
    assert "GROQ_API_KEY" in message
    assert "never-logged-secret" not in message
    assert not path.exists()
