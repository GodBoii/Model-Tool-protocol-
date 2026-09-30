"""Per-conversation state for the TUI.

The app can hold several conversations at once, each with its own session,
backend, agent and run. Everything a run needs lives on its
``Conversation``, so background conversations keep streaming while another
one is on screen.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from textual.app import ComposeResult
from textual.containers import Vertical

from .tui_state import TURN_COMPLETED, ChatResult, TUIState
from .tui_widgets.chat_log import ChatLog, ChatMessage
from .tui_widgets.spinner_widget import SpinnerWidget

if TYPE_CHECKING:
    from textual.timer import Timer
    from textual.widget import Widget
    from textual.worker import Worker

    from .tui_codex_backend import CodexRunHandle


@dataclass
class LiveTurn:
    """The turn a conversation is currently running."""

    raw_prompt: str
    display_prompt: str
    display_attachments: list[str]
    backend: str
    model_name: str
    status: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    thinking_text: str = ""
    tool_events: list[str] = field(default_factory=list)
    tool_details: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steered: list[str] = field(default_factory=list)
    last_render_at: float = 0.0
    flush_timer: Timer | None = None
    # User message and banner mounted for this turn, removed or kept on finish.
    widgets: list[Widget] = field(default_factory=list)
    mutated_workspace: bool = False

    def has_output(self) -> bool:
        return bool(self.blocks or self.tool_events or self.tool_details or self.warnings)

    def text(self) -> str:
        return "".join(str(b.get("text") or "") for b in self.blocks if b.get("type") == "text")

    def append_thinking(self, text: str) -> None:
        if not text:
            return
        self.thinking_text += text
        if self.blocks and self.blocks[-1].get("type") == "thinking":
            self.blocks[-1]["text"] = str(self.blocks[-1].get("text") or "") + text
            return
        self.blocks.append({"type": "thinking", "text": text})

    def append_text(self, text: str) -> None:
        if not text:
            return
        if self.blocks and self.blocks[-1].get("type") == "text":
            self.blocks[-1]["text"] = str(self.blocks[-1].get("text") or "") + text
            return
        self.blocks.append({"type": "text", "text": text})

    def ensure_tool_group(self, *, batch_index: Any = None, mode: str | None = None) -> dict[str, Any]:
        if self.blocks and self.blocks[-1].get("type") == "tool_group":
            block = self.blocks[-1]
            if batch_index is None or block.get("batch_index") == batch_index:
                if mode and not block.get("mode"):
                    block["mode"] = mode
                return block
        block = {"type": "tool_group", "batch_index": batch_index, "mode": mode or "unknown", "items": []}
        self.blocks.append(block)
        return block

    def upsert_tool_item(self, detail: dict[str, Any]) -> None:
        dtype = str(detail.get("type") or "")
        if dtype == "batch_started":
            self.ensure_tool_group(batch_index=detail.get("batch_index"), mode=str(detail.get("mode") or "unknown"))
            return
        if dtype not in {"tool_started", "tool_finished"}:
            return
        status = "running" if dtype == "tool_started" else ("completed" if detail.get("success") else "failed")
        call_id = str(detail.get("call_id") or detail.get("tool_name") or "tool")
        for block in self.blocks:
            if block.get("type") != "tool_group":
                continue
            for item in block.get("items") or []:
                if str(item.get("call_id") or "") == call_id:
                    item["status"] = status
                    item["reasoning"] = detail.get("reasoning")
                    item["cached"] = detail.get("cached")
                    item["error"] = detail.get("error")
                    for key in ("result_preview", "started_at_ms", "finished_at_ms"):
                        if detail.get(key) is not None:
                            item[key] = detail.get(key)
                    return
        block = self.ensure_tool_group(batch_index=detail.get("batch_index"))
        block["items"].append({
            "call_id": call_id,
            "tool_name": str(detail.get("tool_name") or "tool"),
            "status": status,
            "reasoning": detail.get("reasoning"),
            "cached": detail.get("cached"),
            "error": detail.get("error"),
            "started_at_ms": detail.get("started_at_ms"),
            "finished_at_ms": detail.get("finished_at_ms"),
            "result_preview": detail.get("result_preview"),
        })

    def message(self, *, show_tool_details: bool) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            text="",
            model=self.model_name,
            backend=self.backend,
            tool_events=list(self.tool_events),
            tool_details=list(self.tool_details),
            warnings=list(self.warnings),
            thinking=self.thinking_text.strip(),
            show_tool_details=show_tool_details,
            assistant_blocks=list(self.blocks),
            collapse_thinking=False,
            is_live=True,
        )

    def partial_result(self, status: str, error: str | None) -> ChatResult:
        """What streamed so far, for a run that ended without a result."""
        return ChatResult(
            text=self.text(),
            tool_events=list(self.tool_events),
            attachments=list(self.display_attachments),
            warnings=list(self.warnings),
            usage_lines=[],
            tool_details=list(self.tool_details),
            assistant_blocks=list(self.blocks),
            thinking_text=self.thinking_text.strip(),
            status=status,
            error=error,
        )


class ConversationView(Vertical):
    """Chat log plus run spinner for one conversation."""

    DEFAULT_CSS = """
    ConversationView {
        height: 1fr;
        width: 1fr;
    }
    ConversationView > ChatLog {
        height: 1fr;
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield ChatLog(classes="chat-log")
        yield SpinnerWidget(classes="run-spinner")

    @property
    def chat_log(self) -> ChatLog:
        return self.query_one(ChatLog)

    @property
    def spinner(self) -> SpinnerWidget:
        return self.query_one(SpinnerWidget)


@dataclass(frozen=True, slots=True)
class QueuedPrompt:
    """A prompt waiting for the conversation's current run to finish."""

    text: str
    id: str = field(default_factory=lambda: uuid4().hex[:8])


class Conversation:
    """One chat: its session state, on-screen view and in-flight run."""

    def __init__(self, state: TUIState, *, number: int) -> None:
        self.id = uuid4().hex[:8]
        self.number = number
        self.state = state
        self.view = ConversationView(id=f"conv-{self.id}")
        self.live: LiveTurn | None = None
        self.run_id: str | None = None
        self.worker: Worker[ChatResult] | None = None
        self.codex_handle: CodexRunHandle | None = None
        self.queue: deque[QueuedPrompt] = deque()
        # Set when a background run finishes; cleared when the tab is shown.
        self.unread = False
        self.last_status = TURN_COMPLETED

    @property
    def tab_id(self) -> str:
        return f"tab-{self.id}"

    @property
    def running(self) -> bool:
        return self.run_id is not None

    @property
    def title(self) -> str:
        label = (self.state.session_label or "").strip()
        if label:
            return label[:24]
        return "New chat" if not self.state.transcript and self.live is None else f"Chat {self.number}"

    def end_run(self) -> None:
        self.run_id = None
        self.worker = None
        self.codex_handle = None
