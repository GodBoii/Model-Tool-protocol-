from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class APIKeyDialog(ModalScreen[str | None]):
    """Password-style API-key entry that stays outside the chat editor."""

    DEFAULT_CSS = """
    APIKeyDialog { align: center middle; background: rgba(12, 12, 14, 0.75); }
    #api-key-dialog {
        width: 58; height: auto; background: #18181b;
        border: tall #c084fc; padding: 1 2;
    }
    #api-key-dialog-title { color: #f4f4f6; padding-bottom: 1; }
    #api-key-dialog-help { color: #71717a; padding-bottom: 1; }
    #api-key-input { width: 100%; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, provider_name: str) -> None:
        super().__init__()
        self._provider_name = provider_name

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="api-key-dialog"):
                yield Static(f"Set {self._provider_name} API key", id="api-key-dialog-title")
                yield Static(
                    "The key is hidden and stored in your operating-system credential vault.",
                    id="api-key-dialog-help",
                )
                yield Input(password=True, placeholder="Paste API key and press Enter", id="api-key-input")

    def on_mount(self) -> None:
        self.query_one("#api-key-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        event.value = ""
        event.input.value = ""
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.query_one("#api-key-input", Input).value = ""
        self.dismiss(None)
