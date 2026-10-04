"""Status bar widgets for the Textual TUI."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual import events
from textual.widgets import Static
from rich.text import Text


class ThinkingBadge(Static):
    """Clickable compact badge for thinking / reasoning controls."""

    class Activated(Message):
        def __init__(self) -> None:
            super().__init__()

    DEFAULT_CSS = """
    ThinkingBadge {
        width: auto;
        height: 1;
        padding: 0 1;
        color: #d8b4fe;
    }
    ThinkingBadge.-hidden {
        display: none;
    }
    ThinkingBadge:hover {
        background: #27272a;
    }
    """

    def update_badge(self, *, label: str, value: str, visible: bool) -> None:
        if not visible:
            self.add_class("-hidden")
            self.update("")
            return
        self.remove_class("-hidden")
        text = Text()
        text.append(f"{label}:", style="#71717a")
        text.append(value, style="bold #d8b4fe")
        self.update(text)

    def on_click(self, event: events.Click) -> None:
        if not self.has_class("-hidden"):
            self.post_message(self.Activated())


class StatusBar(Horizontal):
    """Bottom status bar showing model, backend, session, and key hints."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: #18181b;
        color: #71717a;
        padding: 0 2;
        border-top: none;
        layout: horizontal;
    }
    #status-main {
        width: 1fr;
        height: 1;
    }
    #status-sandbox {
        width: auto;
        height: 1;
        padding: 0 1;
    }
    #status-hints {
        width: auto;
        height: 1;
        padding: 0 0 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="status-main")
        yield ThinkingBadge(id="thinking-badge")
        yield Static(id="status-sandbox")
        yield Static(id="status-hints")

    def update_status(
        self,
        *,
        backend: str = "codex",
        model: str = "",
        session_id: str = "",
        mode: str = "code",
        turn_count: int = 0,
        sandbox_mode: str = "workspace-write",
        thinking_label: str | None = None,
        thinking_value: str | None = None,
        is_running: bool = False,
        needs_setup: bool = False,
    ) -> None:
        main = Text()
        main.append(" ● ", style="bold #fbbf24" if needs_setup else "bold #34d399" if backend != "codex" else "bold #c084fc")
        main.append(backend, style="bold #c084fc")
        if needs_setup:
            main.append("  setup needed: /apikey", style="bold #fbbf24")
        if model:
            main.append(f"  {model}", style="#fbbf24")
        main.append("  │  ", style="dim #3f3f46")
        main.append(f"mode:{mode}", style="#818cf8")
        main.append("  │  ", style="dim #3f3f46")
        sid_short = session_id.split("-")[-1][:6] if session_id else "—"
        main.append(f"session:{sid_short}", style="#2dd4bf")
        main.append(f"  turns:{turn_count}", style="dim #71717a")
        self.query_one("#status-main", Static).update(main)

        self.query_one("#thinking-badge", ThinkingBadge).update_badge(
            label=thinking_label or "",
            value=thinking_value or "",
            visible=bool(thinking_label and thinking_value),
        )

        sandbox_colors = {
            "read-only": ("#fbbf24", "🔒"),
            "workspace-write": ("#34d399", "✓"),
            "danger-full-access": ("#f43f5e", "⚠"),
        }
        color, icon = sandbox_colors.get(sandbox_mode, ("#71717a", "?"))
        sandbox = Text()
        sandbox.append(f"{icon} {'permissions ' if backend != 'codex' else ''}{sandbox_mode}", style=color)
        self.query_one("#status-sandbox", Static).update(sandbox)

        hints = Text()
        hints.append("│", style="dim #3f3f46")
        if is_running:
            hints.append("  Ctrl+X", style="#38bdf8")
            hints.append(" interrupt  ", style="dim #71717a")
        hints.append("Ctrl+P", style="#818cf8")
        hints.append(" commands  ", style="dim #71717a")
        hints.append("Ctrl+B", style="#818cf8")
        hints.append(" sidebar", style="dim #71717a")
        self.query_one("#status-hints", Static).update(hints)
