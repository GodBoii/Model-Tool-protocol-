"""Chat Log widget with markdown rendering and compact tool surfaces."""

from __future__ import annotations

import re
from typing import Any

from rich.markdown import Markdown
from rich.text import Text
from textual.widgets import RichLog


class ChatMessage:
    """A single chat message for display."""

    __slots__ = (
        "role",
        "text",
        "model",
        "backend",
        "tool_events",
        "warnings",
        "usage_lines",
        "timestamp",
        "thinking",
        "duration_sec",
        "tool_details",
        "show_tool_details",
    )

    def __init__(
        self,
        role: str,
        text: str,
        *,
        model: str = "",
        backend: str = "",
        tool_events: list[str] | None = None,
        warnings: list[str] | None = None,
        usage_lines: list[str] | None = None,
        timestamp: str = "",
        thinking: str = "",
        duration_sec: float | None = None,
        tool_details: list[dict[str, Any]] | None = None,
        show_tool_details: bool = False,
    ) -> None:
        self.role = role
        self.text = text
        self.model = model
        self.backend = backend
        self.tool_events = tool_events or []
        self.warnings = warnings or []
        self.usage_lines = usage_lines or []
        self.timestamp = timestamp
        self.thinking = thinking
        self.duration_sec = duration_sec
        self.tool_details = tool_details or []
        self.show_tool_details = show_tool_details


class ChatLog(RichLog):
    """Scrollable rich chat log."""

    DEFAULT_CSS = """
    ChatLog {
        scrollbar-size: 1 1;
        scrollbar-color: $accent 40%;
        scrollbar-color-hover: $accent 60%;
        scrollbar-color-active: $accent 80%;
    }
    """

    def __init__(self, **kwargs: Any):
        super().__init__(
            highlight=True,
            markup=True,
            wrap=True,
            auto_scroll=True,
            min_width=40,
            **kwargs,
        )

    def add_user_message(self, text: str, attachments: list[str] | None = None) -> None:
        header = Text()
        header.append("  > ", style="bold #ec4899")
        header.append("You", style="bold #f4f4f6")
        self.write(header)

        if attachments:
            att_text = Text("  ")
            for att in attachments[:5]:
                att_text.append(f" [file] {att} ", style="#38bdf8 on #1e293b")
                att_text.append(" ")
            self.write(att_text)

        self.write(Text(f"  {text}", style="#f4f4f6"))
        self.write(Text(""))

    def add_assistant_message(self, msg: ChatMessage) -> None:
        if msg.thinking:
            self._render_thinking(msg.thinking)

        if msg.tool_events:
            self._render_tool_events(msg.tool_events)
            if msg.show_tool_details and msg.tool_details:
                self._render_tool_details(msg.tool_details)

        if msg.warnings:
            for warning in msg.warnings[:3]:
                warn_text = Text()
                warn_text.append("  ! ", style="bold #fbbf24")
                warn_text.append(warning, style="#fbbf24")
                self.write(warn_text)

        header = Text()
        header.append("  < ", style="bold #8b5cf6")
        header.append("Agent", style="bold #c084fc")
        if msg.model:
            header.append(f"  {msg.model}", style="dim #71717a")
        self.write(header)

        if msg.text:
            try:
                self.write(Markdown(msg.text, code_theme="monokai"))
            except Exception:
                self.write(Text(f"  {msg.text}", style="#f4f4f6"))

        if msg.usage_lines:
            self.write(Text(""))
            self._render_usage(msg.usage_lines, msg.duration_sec)

        self.write(Text(""))

    def add_system_message(self, text: str, style: str = "dim #71717a") -> None:
        self.write(Text(f"  {text}", style=style))

    def add_command_result(self, text: str) -> None:
        cleaned = re.sub(r"\033\[[0-9;]*m", "", text)
        self.write(Text(f"  {cleaned}", style="#a78bfa"))
        self.write(Text(""))

    def _render_thinking(self, thinking: str) -> None:
        header = Text()
        header.append("  +- ", style="#3f3f46")
        header.append("Reasoning", style="bold #38bdf8")
        self.write(header)

        lines = thinking.split(" | ") if " | " in thinking else thinking.splitlines()
        display_lines = lines[:8] if len(lines) > 8 else lines
        for line in display_lines:
            trace_text = Text()
            trace_text.append("  |  ", style="#3f3f46")
            trace_text.append(line.strip()[:200], style="dim #71717a")
            self.write(trace_text)
        if len(lines) > 8:
            self.write(Text(f"  |  ... {len(lines) - 8} more steps", style="dim #71717a italic"))

        self.write(Text("  +-----", style="#3f3f46"))

    def _render_tool_events(self, events: list[str]) -> None:
        self.write(Text(f"  * {len(events)} tool events", style="#a78bfa"))

        visible_events = events[:5]
        for index, event in enumerate(visible_events):
            connector = "\\-" if index == len(visible_events) - 1 and len(events) <= 5 else "+-"
            tool_text = Text()
            tool_text.append(f"  |  {connector} ", style="dim #3f3f46")
            clean = event.replace("🔧 ", "")
            tool_text.append(clean[:120], style="#2dd4bf")
            self.write(tool_text)

        if len(events) > 5:
            more = Text()
            more.append(f"  |  \\- ... {len(events) - 5} more ", style="dim #3f3f46")
            more.append("(/details for metadata)", style="dim italic #818cf8")
            self.write(more)

    def _render_tool_details(self, details: list[dict[str, Any]]) -> None:
        self.write(Text("  tool details", style="bold #818cf8"))
        for detail in details[:12]:
            dtype = str(detail.get("type") or "detail")
            line = Text("  |  ", style="dim #3f3f46")
            if dtype == "plan_received":
                source = detail.get("tool_call_source") or "unknown"
                raw_calls = detail.get("raw_tool_call_count")
                batch_count = detail.get("derived_batch_count")
                modes = ",".join(str(mode) for mode in detail.get("derived_batch_modes") or []) or "-"
                line.append(
                    f"plan source={source} raw_calls={raw_calls} batches={batch_count} modes={modes}",
                    style="#93c5fd",
                )
            elif dtype == "batch_started":
                batch_index = detail.get("batch_index")
                mode = detail.get("mode") or "unknown"
                call_ids = ",".join(str(call_id) for call_id in detail.get("call_ids") or []) or "-"
                line.append(f"batch#{batch_index} mode={mode} call_ids={call_ids}", style="#93c5fd")
            elif dtype == "tool_started":
                tool_name = detail.get("tool_name") or "unknown"
                call_id = detail.get("call_id") or "-"
                depends_on = ",".join(str(dep) for dep in detail.get("depends_on") or []) or "-"
                line.append(
                    f"start {tool_name} call_id={call_id} depends_on={depends_on}",
                    style="#93c5fd",
                )
            elif dtype == "tool_finished":
                tool_name = detail.get("tool_name") or "unknown"
                call_id = detail.get("call_id") or "-"
                success = detail.get("success")
                cached = detail.get("cached")
                line.append(
                    f"finish {tool_name} call_id={call_id} success={success} cached={cached}",
                    style="#93c5fd",
                )
            else:
                line.append(str(detail)[:200], style="#93c5fd")
            self.write(line)
        if len(details) > 12:
            self.write(Text(f"  |  ... {len(details) - 12} more detail items", style="dim #71717a"))

    def _render_usage(self, lines: list[str], duration_sec: float | None = None) -> None:
        metrics: list[str] = []
        for line in lines:
            if line.startswith("thinking="):
                continue
            metrics.append(line)

        if not metrics:
            return

        for metric in metrics:
            match = re.match(r"context_window=([\d,]+)/([\d,]+)", metric)
            if match:
                used = int(match.group(1).replace(",", ""))
                total = int(match.group(2).replace(",", ""))
                self._render_context_bar(used, total, duration_sec)
                break

        compact = "  ".join(metric for metric in metrics[:3] if not metric.startswith("context_window="))
        if compact:
            self.write(Text(f"  {compact}", style="dim #71717a"))

    def _render_context_bar(self, used: int, total: int, duration_sec: float | None = None) -> None:
        bar_width = 20
        pct = min(1.0, used / max(1, total))
        filled = int(pct * bar_width)
        empty = bar_width - filled

        if pct < 0.6:
            color = "#34d399"
        elif pct < 0.85:
            color = "#fbbf24"
        else:
            color = "#f43f5e"

        bar = Text("  ctx ")
        bar.append("#" * filled, style=color)
        bar.append("-" * empty, style="dim #3f3f46")
        bar.append(f" {pct * 100:.0f}% ", style=color)
        bar.append(f"{used:,} / {total:,} tokens", style="dim #71717a")
        if duration_sec is not None:
            bar.append(f"  {duration_sec:.1f}s", style="dim #38bdf8")
        self.write(bar)
