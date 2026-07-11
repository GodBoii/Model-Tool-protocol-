"""Widget-based chat transcript for the TUI."""

from __future__ import annotations

import time
from typing import Any

from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static


_TOOL_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class ChatMessage:
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
        "assistant_blocks",
        "collapse_thinking",
        "is_live",
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
        assistant_blocks: list[dict[str, Any]] | None = None,
        collapse_thinking: bool = True,
        is_live: bool = False,
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
        self.assistant_blocks = assistant_blocks or []
        self.collapse_thinking = collapse_thinking
        self.is_live = is_live


class ClickableHeader(Static):
    class Activated(Message):
        """Raised when clicked."""

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Activated())


class ThinkingBlockWidget(Vertical):
    DEFAULT_CSS = """
    ThinkingBlockWidget {
        height: auto;
        margin: 0 0 1 0;
    }
    ThinkingBlockWidget.-collapsed .thinking-body {
        display: none;
    }
    .thinking-header {
        color: #fbbf24;
    }
    .thinking-header:hover {
        background: #18181b;
    }
    .thinking-body {
        padding: 0 0 0 2;
        color: #71717a;
    }
    """

    def __init__(self, text: str, *, collapsed: bool = True) -> None:
        super().__init__()
        self._text = text
        self._collapsed = collapsed

    def compose(self) -> ComposeResult:
        yield ClickableHeader(classes="thinking-header")
        yield Static(classes="thinking-body")

    def on_mount(self) -> None:
        self.update_block(self._text, collapsed=self._collapsed)

    def on_clickable_header_activated(self, event: ClickableHeader.Activated) -> None:
        self._collapsed = not self._collapsed
        self._apply()

    def update_block(self, text: str, *, collapsed: bool | None = None) -> None:
        self._text = text
        if collapsed is not None:
            self._collapsed = collapsed
        self.query_one(".thinking-body", Static).update(self._text or "_No thinking captured._")
        self._apply()

    def _apply(self) -> None:
        header = Text()
        header.append("  ")
        header.append("+" if self._collapsed else "-", style="#fbbf24")
        header.append(" Thinking", style="bold #fbbf24")
        self.query_one(".thinking-header", ClickableHeader).update(header)
        if self._collapsed:
            self.add_class("-collapsed")
        else:
            self.remove_class("-collapsed")


class ToolCallWidget(Vertical):
    DEFAULT_CSS = """
    ToolCallWidget {
        height: auto;
        margin: 0 0 1 0;
    }
    ToolCallWidget.-collapsed .tool-result {
        display: none;
    }
    .tool-header:hover {
        background: #18181b;
    }
    .tool-result {
        padding: 0 0 0 4;
        color: #93c5fd;
    }
    """

    def __init__(self, item: dict[str, Any]) -> None:
        super().__init__()
        self._item = item
        self._collapsed = True
        self._timer = None
        self._frame_index = 0

    def compose(self) -> ComposeResult:
        yield ClickableHeader(classes="tool-header")
        yield Static(classes="tool-result")

    def on_mount(self) -> None:
        self.update_item(self._item)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    def on_clickable_header_activated(self, event: ClickableHeader.Activated) -> None:
        if not self._item.get("result_preview"):
            return
        self._collapsed = not self._collapsed
        self._apply()

    def update_item(self, item: dict[str, Any]) -> None:
        self._item = item
        status = str(self._item.get("status") or "running")
        if status == "running":
            if self._timer is None:
                self._timer = self.set_interval(0.12, self._tick)
            else:
                self._timer.resume()
        elif self._timer is not None:
            self._timer.pause()
        self._tick()
        result_preview = str(self._item.get("result_preview") or "").strip()
        result_widget = self.query_one(".tool-result", Static)
        if result_preview:
            renderable: Any
            if result_preview.startswith("```"):
                renderable = RichMarkdown(result_preview, code_theme="monokai")
            else:
                renderable = Text(f"  {result_preview}", style="#93c5fd")
            result_widget.update(renderable)
        else:
            result_widget.update("")
        self._apply()

    def _tick(self) -> None:
        status = str(self._item.get("status") or "running")
        tool_name = str(self._item.get("tool_name") or "tool")
        reasoning = str(self._item.get("reasoning") or "").strip()
        started_at_ms = self._item.get("started_at_ms")
        finished_at_ms = self._item.get("finished_at_ms")
        error = str(self._item.get("error") or "").strip()
        cached = bool(self._item.get("cached"))
        result_preview = str(self._item.get("result_preview") or "").strip()
        now_ms = int(time.monotonic() * 1000)

        text = Text("  ")
        if status == "running":
            frame = _TOOL_SPINNER_FRAMES[self._frame_index % len(_TOOL_SPINNER_FRAMES)]
            self._frame_index += 1
            text.append(frame, style="#38bdf8")
        elif status == "completed":
            text.append("✓", style="#34d399")
        else:
            text.append("✕", style="#f43f5e")
        text.append(f" {tool_name}", style="bold #2dd4bf")
        if cached:
            text.append(" cached", style="#818cf8")
        if reasoning:
            text.append(f"  {reasoning[:120]}", style="dim #71717a")
        if started_at_ms is not None:
            end_ms = finished_at_ms if finished_at_ms is not None else now_ms
            text.append(f"  {max(0.0, (int(end_ms) - int(started_at_ms)) / 1000):.1f}s", style="dim #71717a")
        if result_preview:
            text.append("  details", style="#fbbf24")
        if error:
            text.append(f"  {error[:120]}", style="#fbbf24")
        self.query_one(".tool-header", ClickableHeader).update(text)

    def _apply(self) -> None:
        has_result = bool(str(self._item.get("result_preview") or "").strip())
        if self._collapsed or not has_result:
            self.add_class("-collapsed")
        else:
            self.remove_class("-collapsed")


class ToolGroupWidget(Vertical):
    DEFAULT_CSS = """
    ToolGroupWidget {
        height: auto;
        margin: 0 0 1 0;
    }
    .tool-group-header {
        color: #a78bfa;
        padding: 0 0 0 1;
    }
    """

    def __init__(self, block: dict[str, Any]) -> None:
        super().__init__()
        self._block = block
        self._tool_widgets: dict[str, ToolCallWidget] = {}

    def compose(self) -> ComposeResult:
        yield Static(classes="tool-group-header")

    def on_mount(self) -> None:
        self.update_group(self._block)

    def update_group(self, block: dict[str, Any]) -> None:
        self._block = block
        mode = str(self._block.get("mode") or "sequential")
        batch_index = self._block.get("batch_index")
        header = Text()
        header.append("  ")
        header.append(f"{mode.title()} tools", style="bold #a78bfa")
        if batch_index is not None:
            header.append(f"  batch {batch_index}", style="dim #71717a")
        self.query_one(".tool-group-header", Static).update(header)
        for item in self._block.get("items") or []:
            if not isinstance(item, dict):
                continue
            call_id = str(item.get("call_id") or item.get("tool_name") or "")
            widget = self._tool_widgets.get(call_id)
            if widget is None:
                widget = ToolCallWidget(item)
                self.mount(widget)
                self._tool_widgets[call_id] = widget
            else:
                widget.update_item(item)


class AssistantMessageWidget(Vertical):
    DEFAULT_CSS = """
    AssistantMessageWidget {
        height: auto;
        margin: 0 0 1 0;
    }
    .assistant-header {
        height: auto;
    }
    .assistant-detail {
        height: auto;
        color: #93c5fd;
        padding: 0 0 0 2;
    }
    """

    def __init__(self, msg: ChatMessage) -> None:
        super().__init__()
        self._msg = msg
        self._thinking_widgets: list[ThinkingBlockWidget] = []
        self._tool_group_widgets: list[ToolGroupWidget] = []
        self._text_widgets: list[Static] = []
        self._warning_widgets: list[Static] = []
        self._detail_widgets: list[Static] = []
        self._block_types: list[str] = []
        self._block_widgets: list[ThinkingBlockWidget | ToolGroupWidget | Static] = []
        self._header_signature: tuple[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Static(classes="assistant-header")

    def on_mount(self) -> None:
        self.update_message(self._msg)

    def update_message(self, msg: ChatMessage) -> None:
        self._msg = msg
        header_signature = (self._msg.model, self._msg.backend)
        if header_signature != self._header_signature:
            header = Text()
            header.append("  < ", style="bold #8b5cf6")
            header.append("Agent", style="bold #c084fc")
            if self._msg.model:
                header.append(f"  {self._msg.model}", style="dim #71717a")
            self.query_one(".assistant-header", Static).update(header)
            self._header_signature = header_signature

        blocks = list(self._msg.assistant_blocks)
        if not blocks:
            if self._msg.thinking:
                blocks.append({"type": "thinking", "text": self._msg.thinking})
            if self._msg.text:
                blocks.append({"type": "text", "text": self._msg.text})

        blocks = [block for block in blocks if str(block.get("type") or "") in {"thinking", "tool_group", "text"}]
        block_types = [str(block.get("type") or "") for block in blocks]
        existing_count = len(self._block_types)
        can_reconcile = self._block_types == block_types[:existing_count]
        if not can_reconcile:
            for widget in self._block_widgets:
                widget.remove()
            self._thinking_widgets = []
            self._tool_group_widgets = []
            self._text_widgets = []
            self._block_widgets = []
            existing_count = 0
        self._block_types = block_types

        for index, block in enumerate(blocks):
            block_type = str(block.get("type") or "")
            if block_type == "thinking":
                text = str(block.get("text") or "")
                collapsed = self._msg.collapse_thinking and not self._msg.is_live
                if index < existing_count:
                    widget = self._block_widgets[index]
                    assert isinstance(widget, ThinkingBlockWidget)
                    widget.update_block(text, collapsed=collapsed)
                else:
                    widget = ThinkingBlockWidget(text, collapsed=collapsed)
                    self.mount(widget)
                    self._thinking_widgets.append(widget)
                    self._block_widgets.append(widget)
            elif block_type == "tool_group":
                if index < existing_count:
                    widget = self._block_widgets[index]
                    assert isinstance(widget, ToolGroupWidget)
                    widget.update_group(block)
                else:
                    widget = ToolGroupWidget(block)
                    self.mount(widget)
                    self._tool_group_widgets.append(widget)
                    self._block_widgets.append(widget)
            elif block_type == "text":
                text = str(block.get("text") or "")
                # Parsing an ever-growing Markdown document for every token is
                # quadratic in practice. Keep streaming updates cheap, then do
                # one rich render when the completed message is installed.
                renderable: Any = (
                    Text(text) if self._msg.is_live else RichMarkdown(text, code_theme="monokai")
                )
                if index < existing_count:
                    widget = self._block_widgets[index]
                    assert isinstance(widget, Static)
                    widget.update(renderable)
                else:
                    widget = Static(renderable)
                    self.mount(widget)
                    self._text_widgets.append(widget)
                    self._block_widgets.append(widget)

        # Warnings and optional diagnostics are small and infrequent; rebuild
        # only these auxiliary rows without disturbing the streaming blocks.
        for widget in [*self._warning_widgets, *self._detail_widgets]:
            widget.remove()
        self._warning_widgets = []
        self._detail_widgets = []

        for warning in self._msg.warnings[:3]:
            widget = Static(f"  ! {warning}")
            self.mount(widget)
            self._warning_widgets.append(widget)

        if self._msg.show_tool_details and self._msg.tool_details:
            for detail in self._msg.tool_details[:12]:
                widget = Static(f"  detail: {_format_tool_detail_line(detail)}", classes="assistant-detail")
                self.mount(widget)
                self._detail_widgets.append(widget)


class UserMessageWidget(Vertical):
    DEFAULT_CSS = """
    UserMessageWidget {
        height: auto;
        margin: 0 0 1 0;
    }
    .user-header {
        height: auto;
    }
    .user-attachments {
        height: auto;
        color: #38bdf8;
    }
    .user-body {
        height: auto;
        color: #f4f4f6;
    }
    """

    def __init__(self, text: str, attachments: list[str] | None = None) -> None:
        super().__init__()
        self._text = text
        self._attachments = attachments or []

    def compose(self) -> ComposeResult:
        header = Text()
        header.append("  > ", style="bold #ec4899")
        header.append("You", style="bold #f4f4f6")
        yield Static(header, classes="user-header")
        if self._attachments:
            att_text = Text("  ")
            for att in self._attachments[:5]:
                att_text.append(f"[file] {att} ", style="#38bdf8 on #1e293b")
                att_text.append(" ")
            yield Static(att_text, classes="user-attachments")
        yield Static(f"  {self._text}", classes="user-body")


class SystemMessageWidget(Static):
    DEFAULT_CSS = """
    SystemMessageWidget {
        height: auto;
        color: #71717a;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, text: Any, *, style: str = "dim #71717a") -> None:
        renderable = text if not isinstance(text, str) else Text(str(text), style=style)
        super().__init__(renderable)


class ChatLog(VerticalScroll):
    DEFAULT_CSS = """
    ChatLog {
        background: $surface;
        border: none;
        padding: 1 2;
        scrollbar-size: 1 1;
        scrollbar-color: $accent 30%;
        scrollbar-color-hover: $accent 50%;
        scrollbar-color-active: $accent 70%;
    }
    #chat-log-body {
        height: auto;
        width: 1fr;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._live_widget: AssistantMessageWidget | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="chat-log-body")

    def clear(self) -> None:
        self._live_widget = None
        body = self.query_one("#chat-log-body", Vertical)
        for child in list(body.children):
            child.remove()

    def add_user_message(self, text: str, attachments: list[str] | None = None) -> None:
        self.query_one("#chat-log-body", Vertical).mount(UserMessageWidget(text, attachments))
        self.scroll_end(animate=False)

    def add_assistant_message(self, msg: ChatMessage) -> None:
        widget = AssistantMessageWidget(msg)
        self.query_one("#chat-log-body", Vertical).mount(widget)
        self.scroll_end(animate=False)

    def set_live_assistant_message(self, msg: ChatMessage) -> None:
        body = self.query_one("#chat-log-body", Vertical)
        if self._live_widget is None:
            self._live_widget = AssistantMessageWidget(msg)
            body.mount(self._live_widget)
        else:
            self._live_widget.update_message(msg)
        self.scroll_end(animate=False)

    def clear_live_assistant_message(self) -> None:
        if self._live_widget is not None:
            self._live_widget.remove()
            self._live_widget = None

    def add_system_message(self, text: Any, style: str = "dim #71717a") -> None:
        self.query_one("#chat-log-body", Vertical).mount(SystemMessageWidget(text, style=style))
        self.scroll_end(animate=False)

    def add_command_result(self, text: str) -> None:
        self.query_one("#chat-log-body", Vertical).mount(SystemMessageWidget(f"  {text}", style="#a78bfa"))
        self.scroll_end(animate=False)


def _format_tool_detail_line(detail: dict[str, Any]) -> str:
    dtype = str(detail.get("type") or "detail")
    if dtype == "plan_received":
        source = detail.get("tool_call_source") or "unknown"
        raw_calls = detail.get("raw_tool_call_count")
        batch_count = detail.get("derived_batch_count")
        modes = ",".join(str(mode) for mode in detail.get("derived_batch_modes") or []) or "-"
        return f"plan source={source} raw_calls={raw_calls} batches={batch_count} modes={modes}"
    if dtype == "batch_started":
        batch_index = detail.get("batch_index")
        mode = detail.get("mode") or "unknown"
        call_ids = ",".join(str(call_id) for call_id in detail.get("call_ids") or []) or "-"
        return f"batch#{batch_index} mode={mode} call_ids={call_ids}"
    if dtype == "tool_started":
        tool_name = detail.get("tool_name") or "unknown"
        call_id = detail.get("call_id") or "-"
        depends_on = ",".join(str(dep) for dep in detail.get("depends_on") or []) or "-"
        return f"start {tool_name} call_id={call_id} depends_on={depends_on}"
    if dtype == "tool_finished":
        tool_name = detail.get("tool_name") or "unknown"
        call_id = detail.get("call_id") or "-"
        success = detail.get("success")
        cached = detail.get("cached")
        return f"finish {tool_name} call_id={call_id} success={success} cached={cached}"
    return str(detail)[:200]
