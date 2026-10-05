"""Operate provider setup without a provider client, agent, or network call."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, RichLog
from tui_test_helpers import wait_until

from mtp.cli import tui_app, tui_harness_agent, tui_provider_factory, tui_settings
from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_settings import (
    DEFAULT_PROVIDER_MODELS, PROVIDER_KEY_ENV, is_provider_configured, load_provider_settings,
    provider_settings_path, save_provider_settings, set_provider_api_key,
)
from mtp.cli.tui_widgets.input_area import InputArea
from mtp.cli.tui_widgets.provider_setup import ProviderPicker, ProviderSetup
from mtp.cli.tui_workers import prepare_backend_switch
from test_tui_app_lifecycle import _make_state


@pytest.fixture(autouse=True)
def no_provider_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Setup must never construct a provider/agent or send a chat request")
    monkeypatch.setattr(tui_app, "run_prompt_blocking", forbidden)
    monkeypatch.setattr(tui_harness_agent, "build_harness_agent", forbidden)
    monkeypatch.setattr(tui_provider_factory, "build_tui_provider", forbidden)
    from mtp.cli.tui_widgets import model_picker
    from mtp.cli.tui_model_catalog import ModelCatalog
    monkeypatch.setattr(model_picker, "discover_provider_models", lambda provider, settings: ModelCatalog((DEFAULT_PROVIDER_MODELS[provider],), "fixture model API", "2026-09-30T00:00:00Z"))
    for name in PROVIDER_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)


def test_first_key_configures_default_model_and_lazy_switch(tmp_path):
    state = _make_state(tmp_path)
    path = provider_settings_path(state.session_store.file_path)
    payload = load_provider_settings(path)
    set_provider_api_key(payload, "groq", "test-secret-never-live")
    save_provider_settings(path, payload)
    assert is_provider_configured(load_provider_settings(path), "groq")
    switch = prepare_backend_switch(state, "groq")
    assert switch.backend == "groq" and switch.agent is None
    assert load_provider_settings(path)["providers"]["groq"]["model"] == DEFAULT_PROVIDER_MODELS["groq"]


def test_environment_key_needs_no_saved_model_or_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-env-secret")
    payload = {"providers": {}}
    assert is_provider_configured(payload, "groq")
    assert not payload["providers"]["groq"]["api_key"]


def test_malformed_saved_or_environment_key_is_not_ready(monkeypatch):
    payload = {"providers": {"groq": {"api_key": "bad key"}}}
    assert not is_provider_configured(payload, "groq")
    monkeypatch.setenv("GROQ_API_KEY", "bad\nkey")
    assert not is_provider_configured(payload, "groq")


@pytest.mark.parametrize("key", ["********", "gsk_test...1234", "<key>", "your_api_key", "bad key"])
def test_masked_or_placeholder_keys_are_rejected(key):
    settings = {"providers": {}}
    with pytest.raises(ValueError):
        set_provider_api_key(settings, "groq", key)
    assert not is_provider_configured(settings, "groq")


def test_missing_provider_save_retry_switch_and_restart(tmp_path, monkeypatch):
    async def scenario():
        state = _make_state(tmp_path)
        app = MTPApp(state=state)
        async with app.run_test(size=(100, 32)) as pilot:
            app._dispatch_command("backend", "groq")
            await wait_until(pilot, lambda: isinstance(app.screen, ProviderSetup)
                             and isinstance(app.screen.focused, Input))
            assert isinstance(app.screen, ProviderSetup)
            dialog = app.screen
            key = dialog.query_one("#setup-key", Input)
            assert key.password and not key.value
            await pilot.press("enter")
            assert dialog.query_one("#setup-error").has_class("visible")
            assert app.state.backend == "codex"
            key.value = "test-secret-never-live"
            real_save = tui_settings.save_provider_settings
            from mtp.cli.tui_widgets import provider_setup
            def broken_save(*args):
                raise PermissionError("test filesystem failure")
            monkeypatch.setattr(provider_setup, "save_provider_settings", broken_save)
            await pilot.press("enter")
            assert app.screen is dialog
            assert app.state.backend == "codex"
            assert not provider_settings_path(state.session_store.file_path).exists()
            monkeypatch.setattr(provider_setup, "save_provider_settings", real_save)
            await pilot.press("enter")
            await pilot.pause(.2)
            assert app.state.backend == "groq"
            assert app.state.agent is None
            assert not app.state.transcript
            assert key.value == ""
            assert all("test-secret" not in text for text in app._input_history)
        restarted = MTPApp(state=state)
        async with restarted.run_test(size=(100, 32)) as pilot:
            await pilot.pause(.1)
            assert not isinstance(restarted.screen, ProviderSetup)
            assert restarted.state.backend == "groq" and restarted.state.agent is None
    asyncio.run(scenario())


def test_startup_cancel_and_change_provider(tmp_path):
    async def scenario():
        state = _make_state(tmp_path)
        state.backend = "openai"
        app = MTPApp(state=state)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause(.1)
            assert isinstance(app.screen, ProviderSetup)
            dialog = app.screen
            key = dialog.query_one("#setup-key", Input)
            key.value = "unsaved-secret"
            dialog.query_one("#setup-change").press()
            deadline = asyncio.get_running_loop().time() + 3
            while not isinstance(app.screen, ProviderPicker) and asyncio.get_running_loop().time() < deadline:
                await pilot.pause(.05)
            assert isinstance(app.screen, ProviderPicker)
            assert key.value == ""
            options = app.screen.query_one(OptionList)
            options.highlighted = next(i for i, o in enumerate(options.options) if o.id == "groq")
            await pilot.press("enter")
            deadline = asyncio.get_running_loop().time() + 3
            while not isinstance(app.screen, ProviderSetup) and asyncio.get_running_loop().time() < deadline:
                await pilot.pause(.05)
            assert isinstance(app.screen, ProviderSetup)
            assert app.screen.provider == "groq"
            await pilot.press("escape")
            assert app.state.backend == "openai"
            assert not provider_settings_path(state.session_store.file_path).exists()
            assert isinstance(app.focused, InputArea)
    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (40, 15)])
def test_key_dialog_keyboard_save_and_shortcut_isolation(tmp_path, size):
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=size) as pilot:
            app._dispatch_command("apikey", "groq")
            await pilot.pause(.1)
            dialog = app.screen
            assert isinstance(dialog, ProviderSetup)
            actions = dialog.query_one("#setup-actions")
            assert actions.region.bottom <= size[1]
            assert actions.region.x >= 0 and actions.region.right <= size[0]
            await pilot.press("ctrl+n")
            assert len(app.conversations) == 1
            key = dialog.query_one("#setup-key", Input)
            key.value = "bad key"
            await pilot.press("enter")
            assert app.screen is dialog
            key.value = "test-secret-never-live"
            await pilot.press("enter")
            await pilot.pause(.1)
            assert not isinstance(app.screen, ProviderSetup)
            assert app.state.backend == "codex"
            assert is_provider_configured(load_provider_settings(provider_settings_path(app.state.session_store.file_path)), "groq")
    asyncio.run(scenario())


def test_existing_key_not_prefilled_and_delete_requires_confirmation(tmp_path):
    async def scenario():
        state = _make_state(tmp_path)
        path = provider_settings_path(state.session_store.file_path)
        payload = load_provider_settings(path)
        set_provider_api_key(payload, "groq", "test-secret-never-live")
        save_provider_settings(path, payload)
        app = MTPApp(state=state)
        async with app.run_test(size=(100, 40)) as pilot:
            app._dispatch_command("apikey", "groq")
            await pilot.pause(.1)
            dialog = app.screen
            key = dialog.query_one("#setup-key", Input)
            assert key.value == ""
            dialog.query_one("#setup-delete").press()
            await pilot.pause(.05)
            assert is_provider_configured(load_provider_settings(path), "groq")
            dialog.query_one("#setup-delete").press()
            await pilot.pause(.1)
            assert not is_provider_configured(load_provider_settings(path), "groq")
    asyncio.run(scenario())


def test_local_endpoint_setup_without_key(tmp_path):
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            app._dispatch_command("backend", "ollama")
            await pilot.pause(.2)
            dialog = app.screen
            assert isinstance(dialog, ProviderSetup) and dialog.local
            endpoint = dialog.query_one("#setup-endpoint", Input)
            endpoint.value = "file:///tmp"
            await pilot.press("enter")
            assert app.screen is dialog
            endpoint.value = "http://localhost:11434"
            await pilot.press("enter")
            await pilot.pause(.2)
            assert app.state.backend == "ollama" and app.state.agent is None
            settings = load_provider_settings(provider_settings_path(app.state.session_store.file_path))
            assert is_provider_configured(settings, "ollama")
            assert not settings["providers"]["ollama"]["api_key"]
    asyncio.run(scenario())


def test_local_model_discovery_choice_and_cancel(tmp_path, monkeypatch):
    from mtp.cli.tui_widgets import provider_setup
    from mtp.cli.tui_local_providers import DiscoveryResult, DiscoveredModel
    monkeypatch.setattr(provider_setup, "discover_models", lambda *args, **kwargs: DiscoveryResult(True, [DiscoveredModel("fixture-model")]))
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            app._dispatch_command("backend", "lmstudio")
            await pilot.pause(.2)
            dialog = app.screen
            dialog.query_one("#setup-discover").press()
            await pilot.pause(.2)
            assert isinstance(app.focused, OptionList)
            await pilot.press("enter")
            assert dialog.query_one("#setup-model", Input).value == "fixture-model"
            await pilot.press("enter")
            await pilot.pause(.2)
            assert app.state.backend == "lmstudio" and app.state.agent is None
    asyncio.run(scenario())


def test_legacy_key_command_is_redacted_and_show_is_masked(tmp_path):
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(100, 32)) as pilot:
            app.on_input_area_submitted(InputArea.Submitted("/apikey set groq test-secret-never-live"))
            await pilot.pause(.1)
            assert app._input_history == ["/apikey groq"]
            app._dispatch_command("apikey", "show groq")
            await pilot.pause(.1)
            text = "\n".join(line.text for line in app.query_one("#cmd-log", RichLog).lines)
            assert "test-secret-never-live" not in text
            assert not app.state.transcript
    asyncio.run(scenario())
