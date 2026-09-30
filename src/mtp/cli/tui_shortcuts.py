"""Keyboard shortcuts shown in the TUI's hint bar, sidebar and /help.

Kept in one place so the hints cannot drift from each other. The bindings
themselves live on ``MTPApp.BINDINGS``; tests check the two agree.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Shortcut:
    keys: str
    label: str
    # Shown in the one-line hint under the input.
    in_hint_bar: bool = False


SHORTCUTS: tuple[Shortcut, ...] = (
    Shortcut("Enter", "Send, or queue while a reply runs"),
    Shortcut("Ctrl+X", "Stop the running reply", in_hint_bar=True),
    Shortcut("Ctrl+G", "Send the newest queued message into the running reply"),
    Shortcut("Ctrl+N", "New chat", in_hint_bar=True),
    Shortcut("Alt+1..9", "Switch chat", in_hint_bar=True),
    Shortcut("Alt+Left/Right", "Previous / next chat"),
    Shortcut("Ctrl+P", "Commands", in_hint_bar=True),
    Shortcut("Ctrl+B", "Sidebar", in_hint_bar=True),
    Shortcut("Ctrl+Y", "Copy last reply"),
    Shortcut("Ctrl+L", "Clear the screen"),
    Shortcut("Esc", "Close suggestions, then stop the reply"),
)

_SHORT_LABELS = {
    "Ctrl+X": "stop",
    "Ctrl+N": "new chat",
    "Alt+1..9": "switch",
    "Ctrl+P": "commands",
    "Ctrl+B": "sidebar",
}


def hint_bar_text() -> str:
    return "  " + "   ".join(
        f"{s.keys} {_SHORT_LABELS.get(s.keys, s.label.lower())}" for s in SHORTCUTS if s.in_hint_bar
    )
