from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import pytest

from mtp.cli import tui_provider_factory as factory


def test_normalize_tui_provider_accepts_alias() -> None:
    assert factory.normalize_tui_provider("claude") == "claude"
    assert factory.normalize_tui_provider("Anthropic") == "claude"
    assert factory.normalize_tui_provider("Xiaomi") == "xiaomi"


def test_normalize_tui_provider_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        factory.normalize_tui_provider("unknown-provider")


def test_build_tui_provider_uses_selected_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str | None, str | None, dict | None]] = []

    def _fake_builder(
        model: str,
        api_key: str | None,
        base_url: str | None = None,
        provider_options: dict | None = None,
    ) -> object:
        calls.append(("claude", model, api_key, base_url, provider_options))
        return {"ok": True}

    monkeypatch.setitem(factory.PROVIDER_BUILDERS, "claude", _fake_builder)
    built = factory.build_tui_provider(
        factory.ProviderSelection(provider_name="claude", model_name="claude-sonnet", api_key="test-key")
    )

    assert built == {"ok": True}
    assert calls == [("claude", "claude-sonnet", "test-key", None, None)]


def test_mask_api_key_formats_display() -> None:
    assert factory.mask_api_key(None) == "(env)"
    assert factory.mask_api_key("abcd") == "****"
    assert factory.mask_api_key("abcdefghi") == "abcd...fghi"
