"""Boot Screen Widget — Animated startup splash.

Renders the MTP ASCII logo with a gradient sweep animation
and session info using Textual timers.
"""
from __future__ import annotations

import math

from textual.widgets import Static
from textual.app import ComposeResult
from textual.containers import Center, Vertical
from rich.text import Text


LOGO_LINES = [
    r"  ███╗   ███╗ ████████╗ ██████╗  ",
    r"  ████╗ ████║ ╚══██╔══╝ ██╔══██╗ ",
    r"  ██╔████╔██║    ██║    ██████╔╝ ",
    r"  ██║╚██╔╝██║    ██║    ██╔═══╝  ",
    r"  ██║ ╚═╝ ██║    ██║    ██║      ",
    r"  ╚═╝     ╚═╝    ╚═╝    ╚═╝      ",
]


def _render_gradient_logo(phase: float = 0.0) -> Text:
    """Render logo with animated horizontal gradient sweep."""
    text = Text()
    for idx, raw_line in enumerate(LOGO_LINES):
        for char_idx, ch in enumerate(raw_line):
            t = (char_idx / max(1, len(raw_line) - 1)) + (idx * 0.1) + phase
            t = (math.sin(t * math.pi - math.pi / 2) + 1) / 2

            r = int(236 + t * (6 - 236))
            g = int(72 + t * (182 - 72))
            b = int(153 + t * (212 - 153))

            text.append(ch, style=f"bold rgb({r},{g},{b})")
        text.append("\n")
    return text


class BootLogo(Static):
    """Animated MTP logo with gradient sweep."""

    DEFAULT_CSS = """
    BootLogo {
        width: 100%;
        height: auto;
        content-align: center middle;
        text-align: center;
        padding: 1 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._phase = 0.0

    def on_mount(self) -> None:
        self.update(_render_gradient_logo(0.0))
        self._timer = self.set_interval(1 / 20, self._animate, pause=False)

    def _animate(self) -> None:
        self._phase += 0.08
        self.update(_render_gradient_logo(self._phase))
        if self._phase > 3.0:
            self._timer.stop()
            self.update(_render_gradient_logo(3.0))


class BootInfo(Static):
    """Session/model info shown during boot."""

    DEFAULT_CSS = """
    BootInfo {
        width: 100%;
        height: auto;
        content-align: center middle;
        text-align: center;
        padding: 0 0 1 0;
    }
    """

    def set_info(
        self,
        version: str,
        backend: str,
        model: str,
        session_short: str,
        cwd: str,
        thinking_label: str | None = None,
        thinking_value: str | None = None,
    ) -> None:
        info = Text()
        info.append("Model Tool Protocol", style="dim #71717a")
        info.append(f"  v{version}", style="bold #c084fc")
        info.append("  ·  SDK + Codex CLI bridge\n", style="dim #71717a")
        info.append("\n")

        info.append("─" * 60 + "\n", style="#3f3f46")
        info.append("\n")

        info.append("  model  ", style="dim #71717a")
        info.append(model or "(default)", style="#fbbf24")
        info.append("    backend  ", style="dim #71717a")
        info.append(backend, style="#38bdf8")
        info.append("\n")

        info.append("  session ", style="dim #71717a")
        info.append(session_short, style="#2dd4bf")
        info.append("  cwd ", style="dim #71717a")
        info.append(cwd, style="#f4f4f6")
        if thinking_label and thinking_value:
            info.append("  ", style="dim #71717a")
            info.append(f"{thinking_label} ", style="dim #71717a")
            info.append(thinking_value, style="#d8b4fe")
        info.append("\n\n")

        info.append("  type ", style="dim #71717a")
        info.append("Ctrl+P", style="#818cf8")
        info.append(" for commands  ·  ", style="dim #71717a")
        info.append("@file", style="#38bdf8")
        info.append(" to attach  ·  ", style="dim #71717a")
        info.append("Ctrl+B", style="#818cf8")
        info.append(" sidebar", style="dim #71717a")
        info.append("\n")

        info.append("─" * 60, style="#3f3f46")

        self.update(info)


class BootScreen(Vertical):
    """Full boot splash screen, auto-dismissed."""

    DEFAULT_CSS = """
    BootScreen {
        align: center middle;
        height: auto;
        width: 100%;
        padding: 2 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Center():
            yield BootLogo(id="boot-logo")
        with Center():
            yield BootInfo(id="boot-info")
