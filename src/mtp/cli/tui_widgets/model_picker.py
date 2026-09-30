"""Provider model search, metadata refresh, and explicit custom model entry."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option
from rich.text import Text

from ..tui_model_catalog import cache_model_catalog, discover_provider_models
from ..tui_settings import ensure_provider_entry, get_provider_models, load_provider_settings, save_provider_settings


@dataclass(frozen=True, slots=True)
class ModelSelection:
    model: str | None = None
    configure_keys: bool = False


class ModelPicker(ModalScreen[ModelSelection | None]):
    DEFAULT_CSS = """
    ModelPicker { align: center middle; background: rgba(12,12,14,0.85); }
    #model-picker { width: 72; max-width: 100%; height: 95%; max-height: 32;
        border: round #38bdf8; background: #18181b; padding: 1 2; }
    #model-picker Static { height: auto; }
    #model-source { color: #fbbf24; }
    #model-fields { height: 1fr; }
    #model-options { height: 1fr; min-height: 3; }
    #model-actions { height: 3; }
    #model-actions Button { width: 1fr; min-width: 0; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, provider: str, settings_path: Path, *, refresh: bool = True) -> None:
        super().__init__()
        self.provider = provider
        self.settings_path = settings_path
        self._refresh_on_mount = refresh
        self._models: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="model-picker"):
            yield Static(f"Choose a model · {self.provider}")
            with VerticalScroll(id="model-fields"):
                yield Static("", id="model-source", markup=False)
                yield Input(placeholder="Search model names", id="model-search")
                yield OptionList(id="model-options")
                yield Static("Or enter a full custom/private model ID:")
                yield Input(placeholder="provider/model-name or model-name", id="custom-model")
            with Horizontal(id="model-actions"):
                yield Button("Use ID", variant="primary", id="model-custom")
                yield Button("Refresh", id="model-refresh")
                yield Button("Keys", id="model-keys")
                yield Button("Cancel", id="model-cancel")

    def on_mount(self) -> None:
        self._read_cache()
        self.query_one("#model-search", Input).focus()
        if self._refresh_on_mount:
            self._start_refresh()

    def _read_cache(self) -> None:
        settings = load_provider_settings(self.settings_path)
        entry = ensure_provider_entry(settings, self.provider)
        self._models = get_provider_models(settings, self.provider)
        source = f"Cached {entry.get('catalog_source')} · {entry.get('catalog_fetched_at')}" if entry.get("catalog_fetched_at") else "Offline suggestions and custom IDs; availability has not been checked."
        self.query_one("#model-source", Static).update(source)
        self._filter()

    def _filter(self) -> None:
        query = self.query_one("#model-search", Input).value.strip().lower()
        options = self.query_one("#model-options", OptionList)
        options.clear_options()
        options.add_options([Option(Text(model), id=model) for model in self._models if query in model.lower()])
        options.highlighted = 0 if options.option_count else None

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "model-search":
            self._filter()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "custom-model":
            self._use_custom()
        else:
            self.query_one("#model-options").focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(ModelSelection(model=event.option_id))

    def _use_custom(self) -> None:
        model = self.query_one("#custom-model", Input).value.strip()
        if not model or len(model) > 300 or any(c.isspace() or ord(c) < 32 for c in model):
            self.query_one("#model-source", Static).update("Enter a full model ID without spaces, then use Enter or Use ID.")
            self.query_one("#custom-model", Input).focus()
            return
        self.dismiss(ModelSelection(model=model))

    def _start_refresh(self) -> None:
        self.run_worker(self._refresh(), group="model-catalog", exclusive=True, exit_on_error=False)

    async def _refresh(self) -> None:
        button = self.query_one("#model-refresh", Button)
        button.disabled = True
        self.query_one("#model-source", Static).update("Loading the provider's model catalog. No chat request is sent.")
        try:
            settings = load_provider_settings(self.settings_path)
            result = await asyncio.to_thread(discover_provider_models, self.provider, settings)
            if not self.is_mounted:
                return
            if result.error:
                self.query_one("#model-source", Static).update(result.error)
                return
            # Reload so a concurrent credential edit is never overwritten by metadata.
            settings = load_provider_settings(self.settings_path)
            cache_model_catalog(settings, self.provider, result)
            save_provider_settings(self.settings_path, settings)
            self._read_cache()
            selected = ensure_provider_entry(settings, self.provider).get("model")
            note = "" if selected in result.models else " Current selection is not in this catalog; choose a model or enter a custom ID."
            self.query_one("#model-source", Static).update(f"{len(result.models)} chat model IDs from {result.source}.{note}")
        except OSError:
            if self.is_mounted:
                self.query_one("#model-source", Static).update("Could not save the catalog. Check the settings directory and retry.")
        except Exception:
            if self.is_mounted:
                self.query_one("#model-source", Static).update("Could not load the catalog. Retry or enter a model ID manually.")
        finally:
            if self.is_mounted:
                button.disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "model-custom":
            self._use_custom()
        elif event.button.id == "model-refresh":
            self._start_refresh()
        elif event.button.id == "model-keys":
            self.dismiss(ModelSelection(configure_keys=True))
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)
