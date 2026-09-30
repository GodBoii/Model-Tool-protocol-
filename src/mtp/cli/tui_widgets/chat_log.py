"""Widget-based chat transcript for the TUI."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from .stream_markdown import MarkdownBlock, StreamingMarkdown


_TOOL_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_TOOL_SPINNER_INTERVAL = 0.12


def _tool_spinner_frame() -> str:
    """Frame derived from the clock, so every running tool spins in step."""
    index = int(time.monotonic() / _TOOL_SPINNER_INTERVAL) % len(_TOOL_SPINNER_FRAMES)
    return _TOOL_SPINNER_FRAMES[index]


class SpinnerClock:
    """One shared timer that repaints every running tool row.

    Each ``ToolCallWidget`` used to own a 0.12 s interval; with many tools
    that meant many timers. The clock runs only while something subscribes.
    """

    def __init__(self, owner: Widget) -> None:
        self._owner = owner
        self._subscribers: set[ToolCallWidget] = set()
        self._timer: Timer | None = None

    def subscribe(self, widget: ToolCallWidget) -> None:
        self._subscribers.add(widget)
        if self._timer is None:
            self._timer = self._owner.set_interval(_TOOL_SPINNER_INTERVAL, self._tick)

    def unsubscribe(self, widget: ToolCallWidget) -> None:
        self._subscribers.discard(widget)
        if not self._subscribers and self._timer is not None:
            self._timer.stop()
            self._timer = None

    @property
    def active(self) -> bool:
        return self._timer is not None

    def _tick(self) -> None:
        for widget in list(self._subscribers):
            widget.repaint_header()


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
        self._rendered = False
        self._ready = False

    def compose(self) -> ComposeResult:
        yield ClickableHeader(classes="thinking-header")
        yield Static(classes="thinking-body")

    def on_mount(self) -> None:
        # ``is_mounted`` is only set after on_mount returns.
        self._ready = True
        self.update_block(self._text, collapsed=self._collapsed)

    def on_clickable_header_activated(self, event: ClickableHeader.Activated) -> None:
        self._collapsed = not self._collapsed
        self._apply()

    def update_block(self, text: str, *, collapsed: bool | None = None) -> None:
        if collapsed is not None:
            self._collapsed = collapsed
        if not self._ready:
            self._text = text  # on_mount renders it
            return
        if text != self._text or not self._rendered:
            self._text = text
            self._rendered = True
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
        self._clock: SpinnerClock | None = None
        self._rendered_preview: str | None = None

    def compose(self) -> ComposeResult:
        yield ClickableHeader(classes="tool-header")
        yield Static(classes="tool-result")

    def on_mount(self) -> None:
        try:
            self._clock = self.query_ancestor(ChatLog).spinner_clock
        except NoMatches:
            # Mounted outside a ChatLog (tests, previews): use a private clock.
            self._clock = SpinnerClock(self)
        self.update_item(self._item)

    def on_unmount(self) -> None:
        if self._clock is not None:
            self._clock.unsubscribe(self)

    def on_clickable_header_activated(self, event: ClickableHeader.Activated) -> None:
        if not self._item.get("result_preview"):
            return
        self._collapsed = not self._collapsed
        self._apply()

    def update_item(self, item: dict[str, Any]) -> None:
        self._item = item
        if self._clock is None:
            return  # not mounted yet; on_mount renders the latest item
        if str(self._item.get("status") or "running") == "running":
            self._clock.subscribe(self)
        else:
            self._clock.unsubscribe(self)
        self.repaint_header()
        result_preview = str(self._item.get("result_preview") or "").strip()
        if result_preview != self._rendered_preview:
            # Previews can be 12k chars of JSON; parse them only when they change.
            self._rendered_preview = result_preview
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

    def repaint_header(self) -> None:
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
            text.append(_tool_spinner_frame(), style="#38bdf8")
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
        self._ready = False

    def compose(self) -> ComposeResult:
        yield Static(classes="tool-group-header")

    def on_mount(self) -> None:
        self._ready = True
        self.update_group(self._block)

    def update_group(self, block: dict[str, Any]) -> None:
        self._block = block
        if not self._ready:
            return  # on_mount renders the latest block
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
        # One (block_type, widget) per rendered block, in display order.
        self._block_widgets: list[tuple[str, Widget]] = []
        self._footer_widgets: list[Static] = []
        self._footer_key: tuple[Any, ...] | None = None
        self._header_key: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(classes="assistant-header")

    def on_mount(self) -> None:
        self.update_message(self._msg)

    @staticmethod
    def _blocks_for(msg: ChatMessage) -> list[dict[str, Any]]:
        blocks = [
            block for block in msg.assistant_blocks
            if str(block.get("type") or "") != "text" or str(block.get("text") or "")
        ]
        if blocks:
            return blocks
        fallback: list[dict[str, Any]] = []
        if msg.thinking:
            fallback.append({"type": "thinking", "text": msg.thinking})
        if msg.text:
            fallback.append({"type": "text", "text": msg.text})
        return fallback

    def update_message(self, msg: ChatMessage) -> None:
        """Bring the rendered blocks in line with ``msg``.

        Blocks only grow or change in place while a turn streams, so widgets
        whose type still matches are updated instead of remounted. Frame cost
        then tracks what changed, and spinner and collapse state survive.
        """
        was_live = self._msg.is_live
        self._msg = msg
        self._update_header()

        collapse_now = msg.collapse_thinking and not msg.is_live
        finalized = was_live and not msg.is_live
        blocks = self._blocks_for(msg)

        keep = 0
        for index, block in enumerate(blocks):
            if index >= len(self._block_widgets):
                break
            if self._block_widgets[index][0] != str(block.get("type") or ""):
                break
            keep += 1
        for _, widget in self._block_widgets[keep:]:
            widget.remove()
        del self._block_widgets[keep:]

        new_widgets: list[Widget] = []
        for index, block in enumerate(blocks):
            block_type = str(block.get("type") or "")
            if index < keep:
                self._update_block_widget(block_type, self._block_widgets[index][1], block, finalized, collapse_now)
                continue
            widget = self._create_block_widget(block_type, block, collapse_now, live=msg.is_live)
            if widget is None:
                continue
            self._block_widgets.append((block_type, widget))
            new_widgets.append(widget)

        if new_widgets:
            if self._footer_widgets:
                self.mount_all(new_widgets, before=self._footer_widgets[0])
            else:
                self.mount_all(new_widgets)
        self._update_footer()

    def _update_header(self) -> None:
        model = self._msg.model or ""
        if model == self._header_key:
            return
        self._header_key = model
        header = Text()
        header.append("  < ", style="bold #8b5cf6")
        header.append("Agent", style="bold #c084fc")
        if model:
            header.append(f"  {model}", style="dim #71717a")
        self.query_one(".assistant-header", Static).update(header)

    @staticmethod
    def _create_block_widget(
        block_type: str, block: dict[str, Any], collapsed: bool, *, live: bool,
    ) -> Widget | None:
        if block_type == "thinking":
            return ThinkingBlockWidget(str(block.get("text") or ""), collapsed=collapsed)
        if block_type == "tool_group":
            return ToolGroupWidget(block)
        if block_type == "text":
            text = str(block.get("text") or "")
            return StreamingMarkdown(text) if live else MarkdownBlock(text)
        return None

    @staticmethod
    def _update_block_widget(
        block_type: str, widget: Widget, block: dict[str, Any], finalized: bool, collapse_now: bool,
    ) -> None:
        if block_type == "thinking" and isinstance(widget, ThinkingBlockWidget):
            # Keep the user's expand/collapse choice, except when the turn ends.
            widget.update_block(str(block.get("text") or ""), collapsed=collapse_now if finalized else None)
        elif block_type == "tool_group" and isinstance(widget, ToolGroupWidget):
            widget.update_group(block)
        elif block_type == "text" and isinstance(widget, (StreamingMarkdown, MarkdownBlock)):
            widget.update_text(str(block.get("text") or ""))

    def _update_footer(self) -> None:
        warnings = tuple(self._msg.warnings[:3])
        details: tuple[str, ...] = ()
        if self._msg.show_tool_details:
            details = tuple(_format_tool_detail_line(detail) for detail in self._msg.tool_details[:12])
        key = (warnings, details)
        if key == self._footer_key:
            return
        self._footer_key = key
        for widget in self._footer_widgets:
            widget.remove()
        self._footer_widgets = [Static(f"  ! {warning}") for warning in warnings]
        self._footer_widgets += [Static(f"  detail: {line}", classes="assistant-detail") for line in details]
        if self._footer_widgets:
            self.mount_all(self._footer_widgets)


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


# Turns mounted when a transcript loads; each costs Markdown layout on the
# first paint. Older turns mount HISTORY_PAGE at a time on demand.
HISTORY_WINDOW = 12
HISTORY_PAGE = 20


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    """One saved exchange, ready to render."""

    prompt: str
    attachments: list[str]
    reply: ChatMessage


def _turn_widgets(turn: HistoryTurn) -> list[Widget]:
    return [UserMessageWidget(turn.prompt, turn.attachments), AssistantMessageWidget(turn.reply)]


class EarlierTurnsButton(Static):
    """Top-of-log control that loads hidden older turns."""

    DEFAULT_CSS = """
    EarlierTurnsButton {
        color: #a78bfa;
        margin: 0 0 1 0;
    }
    EarlierTurnsButton:hover {
        background: #18181b;
    }
    """

    class Activated(Message):
        """Raised when clicked."""

    def __init__(self, count: int) -> None:
        super().__init__()
        self._count = count

    def on_mount(self) -> None:
        self.set_count(self._count)

    def set_count(self, count: int) -> None:
        self._count = count
        label = Text("  ")
        label.append(f"^ {count} earlier turn{'s' if count != 1 else ''}", style="bold #a78bfa")
        label.append("  click or scroll up to load", style="dim #71717a")
        self.update(label)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Activated())


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
        self.spinner_clock = SpinnerClock(self)
        # Turns not mounted yet, oldest first. Loaded on demand.
        self._hidden_turns: list[HistoryTurn] = []
        self._earlier_button: EarlierTurnsButton | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="chat-log-body")

    @property
    def hidden_turn_count(self) -> int:
        return len(self._hidden_turns)

    def clear(self) -> None:
        self._live_widget = None
        self._hidden_turns = []
        self._earlier_button = None
        body = self.query_one("#chat-log-body", Vertical)
        body.remove_children()

    def load_history(self, turns: list[HistoryTurn], *, window: int = HISTORY_WINDOW) -> None:
        """Replace the log with ``turns``, mounting only the newest ``window``."""
        self.clear()
        split = max(0, len(turns) - window)
        self._hidden_turns = list(turns[:split])
        body = self.query_one("#chat-log-body", Vertical)
        widgets: list[Widget] = []
        if self._hidden_turns:
            self._earlier_button = EarlierTurnsButton(len(self._hidden_turns))
            widgets.append(self._earlier_button)
        for turn in turns[split:]:
            widgets.extend(_turn_widgets(turn))
        if widgets:
            body.mount_all(widgets)
        self.scroll_end(animate=False)

    def show_earlier(self, count: int = HISTORY_PAGE) -> None:
        """Mount up to ``count`` more of the hidden older turns."""
        if not self._hidden_turns or self._earlier_button is None:
            return
        batch = self._hidden_turns[-count:]
        del self._hidden_turns[-count:]
        widgets: list[Widget] = []
        for turn in batch:
            widgets.extend(_turn_widgets(turn))
        body = self.query_one("#chat-log-body", Vertical)
        body.mount_all(widgets, after=self._earlier_button)
        if self._hidden_turns:
            self._earlier_button.set_count(len(self._hidden_turns))
        else:
            self._earlier_button.remove()
            self._earlier_button = None

    def on_earlier_turns_button_activated(self, event: EarlierTurnsButton.Activated) -> None:
        event.stop()
        self.show_earlier()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        # Reaching the top while older turns are hidden loads the next batch.
        if new_value <= 0 < old_value and self._hidden_turns:
            self.call_after_refresh(self.show_earlier)

    def add_user_message(self, text: str, attachments: list[str] | None = None) -> UserMessageWidget:
        widget = UserMessageWidget(text, attachments)
        self.query_one("#chat-log-body", Vertical).mount(widget)
        self.scroll_end(animate=False)
        return widget

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

    def finalize_live_assistant_message(self, msg: ChatMessage) -> None:
        """Turn the streaming widget into the finished turn in place.

        Avoids rebuilding the whole transcript when a turn completes.
        """
        if self._live_widget is None:
            self.add_assistant_message(msg)
            return
        self._live_widget.update_message(msg)
        self._live_widget = None
        self.scroll_end(animate=False)

    def add_system_message(self, text: Any, style: str = "dim #71717a") -> SystemMessageWidget:
        widget = SystemMessageWidget(text, style=style)
        self.query_one("#chat-log-body", Vertical).mount(widget)
        self.scroll_end(animate=False)
        return widget

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
