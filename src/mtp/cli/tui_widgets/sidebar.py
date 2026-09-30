"""Sidebar Widget — Toggleable workspace context panel."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.widgets import Static, Tree
from textual.containers import VerticalScroll
from textual.app import ComposeResult
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
        except Exception as exc:
            self.root.add(f"⚠ {exc}", allow_expand=False)

    def refresh_tree(self, cwd: Path, *, force: bool = False) -> None:
        """Re-list ``cwd``. Skips the directory scan when nothing changed."""
        if cwd == self._cwd and not force:
            return
        self._cwd = cwd
        self.root.set_label(str(cwd.name or cwd))
        self._populate()


class SessionInfo(Static):
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
        info.append("  ID      ", style="dim #71717a")
        info.append(f"{sid_short}\n", style="#2dd4bf")
        if label:
            info.append("  Label   ", style="dim #71717a")
            info.append(f"{label[:22]}\n", style="#f4f4f6")
        info.append("  Backend ", style="dim #71717a")
        info.append(f"{backend}\n", style="#34d399")
        info.append("  Model   ", style="dim #71717a")
        info.append(f"{model}\n", style="#fbbf24")
        info.append("  Mode    ", style="dim #71717a")
        info.append(f"{mode}\n", style="#818cf8")
        if thinking_label and thinking_value:
            info.append(f"  {thinking_label.title():<8}", style="dim #71717a")
            info.append(f"{thinking_value}\n", style="#d8b4fe")
        info.append("  Turns   ", style="dim #71717a")
        info.append(f"{turn_count}\n", style="#2dd4bf")
        self.update(info)


class RunMetrics(Static):
    DEFAULT_CSS = """
    RunMetrics {
        padding: 1 1;
        height: auto;
    }
    """

    def update_metrics(self, lines: list[str]) -> None:
        text = Text()
        text.append("  Run Metrics\n", style="bold #f472b6")
        if not lines:
            text.append("  No metrics yet", style="dim #71717a")
            self.update(text)
            return
        for raw in lines:
            key, _, value = raw.partition("=")
            label = key.replace("_", " ")
            text.append(f"  {label:<16}", style="dim #71717a")
            text.append(f"{value}\n", style="#93c5fd")
        self.update(text)


class ToolEventLog(Static):
    DEFAULT_CSS = """
    ToolEventLog {
        padding: 1 1;
        height: auto;
        max-height: 15;
    }
    """

    def update_events(self, events: list[str]) -> None:
        text = Text()
        text.append("  Recent Tools\n", style="bold #a78bfa")
        if not events:
            text.append("  No tool events yet", style="dim #71717a")
            self.update(text)
            return
        for index, event in enumerate(events[-8:]):
            connector = "└─" if index == len(events[-8:]) - 1 else "├─"
            clean = event.replace("🔧 ", "")
            text.append(f"  {connector} ", style="dim #3f3f46")
            text.append(f"{clean[:70]}\n", style="#2dd4bf")
        self.update(text)


class ShortcutHints(Static):
    DEFAULT_CSS = """
    ShortcutHints {
        padding: 1 1;
        height: auto;
    }
    """

    def update_hints(self) -> None:
        text = Text()
        text.append("  Shortcuts\n", style="bold #38bdf8")
        from ..tui_shortcuts import SHORTCUTS

        for shortcut in SHORTCUTS:
            text.append(f"  {shortcut.keys:<15}", style="#818cf8")
            text.append(f"{shortcut.label[:18]}\n", style="#f4f4f6")
        self.update(text)


class Sidebar(VerticalScroll):
    DEFAULT_CSS = """
    Sidebar {
        width: 37;
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
        yield RunMetrics(id="run-metrics")
        yield Static("  ─────────", classes="separator")
        yield ToolEventLog(id="tool-event-log")
        yield Static("  ─────────", classes="separator")
        yield ShortcutHints(id="shortcut-hints")
        yield Static("  ─────────", classes="separator")
        yield Static("  Workspace", classes="section-header")
        yield WorkspaceTree(Path.cwd(), id="workspace-tree")

    def toggle(self) -> None:
        self.toggle_class("visible")

    @property
    def is_open(self) -> bool:
        return self.has_class("visible")
