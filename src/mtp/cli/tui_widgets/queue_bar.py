"""Queued messages for the active chat, shown above the input."""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.markup import escape
from textual.widgets import Static

if TYPE_CHECKING:
    from ..tui_conversation import QueuedPrompt

_PREVIEW_CHARS = 70
_MAX_ROWS = 4


class QueueBar(Static):
    """One row per queued message, with click actions to steer or drop it."""

    DEFAULT_CSS = """
    QueueBar {
        height: auto;
        display: none;
        padding: 0 2;
        color: #a1a1aa;
    }
    QueueBar.visible {
        display: block;
    }
    """

    def show_queue(self, items: list[QueuedPrompt], *, can_steer: bool) -> None:
        if not items:
            self.remove_class("visible")
            self.update("")
            return
        rows = [f"[bold #fbbf24]Queued ({len(items)})[/]  [dim]runs after the current reply[/]"]
        for index, item in enumerate(items[:_MAX_ROWS], start=1):
            preview = " ".join(item.text.split())
            if len(preview) > _PREVIEW_CHARS:
                preview = preview[: _PREVIEW_CHARS - 1] + "…"
            actions = f"[@click=app.drop_queued('{item.id}')][#f43f5e]remove[/][/]"
            if can_steer:
                actions = f"[@click=app.steer_queued('{item.id}')][#38bdf8]steer now[/][/]  " + actions
            rows.append(f"  {index}. {escape(preview)}  {actions}")
        if len(items) > _MAX_ROWS:
            rows.append(f"  [dim]+{len(items) - _MAX_ROWS} more (/queue)[/]")
        if can_steer:
            rows.append("  [dim]Ctrl+G steers the newest one into the running reply.[/]")
        self.update("\n".join(rows))
        self.add_class("visible")
