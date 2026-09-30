"""Keyboard and mouse setup flows. Credentials never enter the chat composer."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from ..tui_provider_factory import SUPPORTED_TUI_PROVIDERS
from ..tui_settings import (
    PROVIDER_KEY_ENV, delete_provider_api_key, ensure_provider_entry,
    load_provider_settings, preferred_model_for_provider, provider_api_key, provider_setup_status,
    save_provider_settings, set_provider_api_key,
)


class ProviderPicker(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ProviderPicker { align: center middle; background: rgba(12,12,14,0.85); }
    #provider-picker { width: 68; max-width: 100%; height: auto; max-height: 90%;
        border: round #38bdf8; background: #18181b; padding: 1 2; }
    #provider-options { height: auto; max-height: 12; margin: 1 0; }
    #provider-picker Button { width: 100%; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, settings_path: Path, active: str, *, managing_keys: bool = False) -> None:
        super().__init__()
        self.settings_path = settings_path
        self.active = active
        self.managing_keys = managing_keys

    def compose(self) -> ComposeResult:
        settings = load_provider_settings(self.settings_path)
        with VerticalScroll(id="provider-picker"):
            yield Static("Manage provider keys" if self.managing_keys else "Choose a provider")
            yield Static("Arrows to select, Enter to open setup. Esc cancels.")
            yield OptionList(*[
                Option(f"{'●' if name == self.active else '○'} {name}  ·  {provider_setup_status(settings, name)}", id=name)
                for name in ("codex", *sorted(SUPPORTED_TUI_PROVIDERS))
            ], id="provider-options")
            yield Button("Cancel", id="provider-cancel")

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.highlighted = next((i for i, option in enumerate(options.options) if option.id == self.active), 0)
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProviderSetup(ModalScreen[str | None]):
    """Save locally, change provider, or cancel; failed writes keep the form open."""
    DEFAULT_CSS = """
    ProviderSetup { align: center middle; background: rgba(12,12,14,0.85); }
    #provider-setup { width: 70; max-width: 100%; height: auto; max-height: 95%;
        border: round #38bdf8; background: #18181b; padding: 1 2; }
    #provider-setup Label { margin-top: 1; height: auto; color: #e4e4e7; }
    #provider-setup Static { height: auto; }
    #setup-title { text-style: bold; color: #f4f4f6; }
    #setup-message { color: #fbbf24; margin-top: 1; }
    #setup-error { color: #fb7185; margin-top: 1; display: none; }
    #setup-error.visible { display: block; }
    #provider-setup Button { width: 100%; margin-top: 1; }
    #key-visibility { height: 1; min-height: 1; border: none; margin-top: 0; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, provider: str, settings_path: Path, *, switching: bool = False) -> None:
        super().__init__()
        self.provider = provider
        self.settings_path = settings_path
        self.switching = switching
        self.local = provider in {"ollama", "lmstudio"}
        self._delete_confirmed = False

    def compose(self) -> ComposeResult:
        settings = load_provider_settings(self.settings_path)
        entry = ensure_provider_entry(settings, self.provider)
        with VerticalScroll(id="provider-setup"):
            yield Static(f"Set up {self.provider}", id="setup-title")
            yield Static(
                "Set the server endpoint and model. Local servers usually do not need a key."
                if self.local else f"Add an API key to use {self.provider}, or choose another provider.",
                id="setup-message",
            )
            yield Static(provider_setup_status(settings, self.provider), id="setup-key-status")
            yield Static("Enter saves. Tab moves between fields. Esc cancels.")
            if self.local:
                default_url = "http://localhost:11434" if self.provider == "ollama" else "http://localhost:1234/v1"
                yield Label("Server endpoint", markup=False)
                yield Input(str(entry.get("base_url") or default_url), id="setup-endpoint")
            yield Label("API key (optional for local servers)" if self.local else "API key", markup=False)
            yield Input(password=True, placeholder="Paste a key, or leave empty to keep the existing key", id="setup-key")
            yield Button("Show key", id="key-visibility")
            yield Static(f"You can also use {PROVIDER_KEY_ENV[self.provider]} in your environment.")
            yield Label("Model", markup=False)
            yield Input(preferred_model_for_provider(settings, self.provider), id="setup-model")
            yield Static("Keys are saved locally and are never included in chat history.")
            yield Static("", id="setup-error", markup=False)
            yield Button(f"Save and use {self.provider}" if self.switching else "Save settings", variant="primary", id="setup-save")
            if entry.get("api_key"):
                yield Button("Remove saved key", variant="error", id="setup-delete")
            yield Button("Choose another provider", id="setup-change")
            yield Button("Cancel", id="setup-cancel")

    def on_mount(self) -> None:
        self.query_one("#setup-endpoint" if self.local else "#setup-key", Input).focus()

    def _error(self, message: str, selector: str | None = None) -> None:
        error = self.query_one("#setup-error", Static)
        error.update(message)
        error.add_class("visible")
        error.scroll_visible()
        if selector:
            self.query_one(selector, Input).focus()

    def _save(self) -> None:
        settings = load_provider_settings(self.settings_path)
        entry = ensure_provider_entry(settings, self.provider)
        key = self.query_one("#setup-key", Input).value.strip()
        model = self.query_one("#setup-model", Input).value.strip()
        if not model or any(char.isspace() for char in model):
            self._error("Enter a model name without spaces.", "#setup-model")
            return
        if key:
            try:
                set_provider_api_key(settings, self.provider, key)
            except ValueError as exc:
                self._error(str(exc), "#setup-key")
                return
        elif not self.local and not provider_api_key(settings, self.provider):
            self._error("Paste an API key, or choose another provider.", "#setup-key")
            return
        if self.local:
            endpoint = self.query_one("#setup-endpoint", Input).value.strip()
            try:
                parsed = urlsplit(endpoint)
                valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
                _ = parsed.port
            except ValueError:
                valid = False
            if not valid or any(c.isspace() for c in endpoint) or parsed.query or parsed.fragment:
                self._error("Enter an http:// or https:// server URL without credentials, query, or fragment.", "#setup-endpoint")
                return
            entry["base_url"] = endpoint.rstrip("/")
            entry["deployment_type"] = "local"
        entry["model"] = model
        try:
            save_provider_settings(self.settings_path, settings)
        except OSError:
            self._error("Could not save settings. Check access to the session directory and try again.")
            return
        self.query_one("#setup-key", Input).value = ""
        self.dismiss("saved")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = event.button.id
        if button_id == "setup-save":
            self._save()
        elif button_id == "key-visibility":
            key = self.query_one("#setup-key", Input)
            key.password = not key.password
            event.button.label = "Show key" if key.password else "Hide key"
            key.focus()
        elif button_id == "setup-change":
            self.query_one("#setup-key", Input).value = ""
            self.dismiss("change")
        elif button_id == "setup-delete":
            if not self._delete_confirmed:
                self._delete_confirmed = True
                event.button.label = "Confirm removal of saved key"
                return
            settings = load_provider_settings(self.settings_path)
            delete_provider_api_key(settings, self.provider)
            try:
                save_provider_settings(self.settings_path, settings)
            except OSError:
                self._error("Could not remove the key. Check access to the session directory and try again.")
                return
            self.query_one("#setup-key", Input).value = ""
            self.dismiss("deleted")
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.query_one("#setup-key", Input).value = ""
        self.dismiss(None)
