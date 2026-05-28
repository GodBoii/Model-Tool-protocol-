"""Sidebar Widget — Toggleable workspace context panel.

Shows session info, recent tool events, and workspace file tree.
Can be shown/hidden with Ctrl+B.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.widgets import Static, Tree
from textual.containers import Vertical, VerticalScroll
from textual.app import ComposeResult
from textual.reactive import reactive
from rich.text import Text


class WorkspaceTree(Tree):
    """Lightweight file tree showing workspace root structure."""

    DEFAULT_CSS = """
    WorkspaceTree {
        height: auto;
        max-height: 20;
        padding: 0 1;
        scrollbar-size: 1 1;
    }
    """

    def __init__(self, cwd: Path, **kwargs: Any) -> None:
        super().__init__(label=str(cwd.name or cwd), **kwargs)
        self._cwd = cwd

    def on_mount(self) -> None:
        self._populate()

    def _populate(self) -> None:
        self.clear()
        self.root.expand()
        try:
            entries = sorted(self._cwd.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            for entry in entries[:30]:
                if entry.name.startswith(".") and entry.name not in {".env", ".gitignore"}:
                    continue
                if entry.is_dir():
                    self.root.add(f"📁 {entry.name}", allow_expand=False)
                else:
                    size_kb = entry.stat().st_size / 1024
                    suffix = f" ({size_kb:.0f}KB)" if size_kb > 10 else ""
                    self.root.add(f"📄 {entry.name}{suffix}", allow_expand=False)
        except PermissionError:
            self.root.add("⚠ Permission denied", allow_expand=False)
        except Exception as e:
            self.root.add(f"⚠ {e}", allow_expand=False)

    def refresh_tree(self, cwd: Path) -> None:
        self._cwd = cwd
        self.root.set_label(str(cwd.name or cwd))
        self._populate()


class SessionInfo(Static):
    """Displays current session details."""

    DEFAULT_CSS = """
    SessionInfo {
        padding: 1 1;
        height: auto;
    }
    """

    def update_info(
        self,
        *,
        session_id: str = "",
        label: str = "",
        backend: str = "",
        model: str = "",
        turn_count: int = 0,
        mode: str = "",
        thinking_label: str | None = None,
        thinking_value: str | None = None,
    ) -> None:
        info = Text()
        info.append("  Session\n", style="bold #c084fc")

        sid_short = session_id.split("-")[-1][:8] if session_id else "—"
        info.append(f"  ID      ", style="dim #71717a")
        info.append(f"{sid_short}\n", style="#2dd4bf")

        if label:
            info.append(f"  Label   ", style="dim #71717a")
            info.append(f"{label}\n", style="#f4f4f6")

        info.append(f"  Backend ", style="dim #71717a")
        info.append(f"{backend}\n", style="#34d399")

        info.append(f"  Model   ", style="dim #71717a")
        info.append(f"{model}\n", style="#fbbf24")

        info.append(f"  Mode    ", style="dim #71717a")
        info.append(f"{mode}\n", style="#818cf8")

        if thinking_label and thinking_value:
            info.append(f"  {thinking_label.title():<8}", style="dim #71717a")
            info.append(f"{thinking_value}\n", style="#d8b4fe")

        info.append(f"  Turns   ", style="dim #71717a")
        info.append(f"{turn_count}\n", style="#2dd4bf")

        self.update(info)


class ToolEventLog(Static):
    """Shows recent tool events from the last turn."""

    DEFAULT_CSS = """
    ToolEventLog {
        padding: 1 1;
        height: auto;
        max-height: 15;
    }
    """

    def update_events(self, events: list[str]) -> None:
        if not events:
            text = Text("  No tool events yet", style="dim #71717a")
            self.update(text)
            return

        text = Text()
        text.append("  Recent Tools\n", style="bold #a78bfa")
        for i, event in enumerate(events[-8:]):
            connector = "└─" if i == len(events[-8:]) - 1 else "├─"
            clean = event.replace("🔧 ", "")
            text.append(f"  {connector} ", style="dim #3f3f46")
            text.append(f"{clean[:50]}\n", style="#2dd4bf")
        self.update(text)


class Sidebar(VerticalScroll):
    """Toggleable right sidebar for workspace context."""

    DEFAULT_CSS = """
    Sidebar {
        width: 35;
        dock: right;
        background: #0c0c0e;
        border-left: tall #27272a;
        padding: 0;
        display: none;
        overflow-y: auto;
        scrollbar-size: 1 1;
    }
    Sidebar.visible {
        display: block;
    }
    """

    def compose(self) -> ComposeResult:
        yield SessionInfo(id="session-info")
        yield Static("  ─────────", classes="separator")
        yield ToolEventLog(id="tool-event-log")
        yield Static("  ─────────", classes="separator")
        yield Static("  Workspace", classes="section-header")

    def toggle(self) -> None:
        self.toggle_class("visible")
