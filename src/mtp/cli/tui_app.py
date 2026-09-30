"""MTP TUI App — Main Textual Application.

Async-first, modular Textual App replacing the monolithic tui.py loop.
All LLM calls run in Worker threads; UI never blocks.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
import time
from typing import Any, Callable, TypeVar
from uuid import uuid4

import dataclasses

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, OptionList, RichLog, Tab, Tabs
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.worker import Worker, WorkerState
from textual import events

from .tui_state import (
    TURN_CANCELLED, TURN_COMPLETED, TURN_FAILED,
    TUIState, ChatResult, TranscriptTurn, active_model_name,
    new_session_id, generate_session_title_from_prompt,
    resolve_model,
)
from .tui_settings import pop_settings_recoveries
from .tui_shortcuts import SHORTCUTS
from .tui_thinking import apply_thinking_value, get_thinking_capability
from .tui_widgets.chat_log import ChatLog, ChatMessage, HistoryTurn
from .tui_widgets.input_area import InputPanel, InputArea, PromptLabel, AttachmentBadge
from .tui_widgets.status_bar import StatusBar, ThinkingBadge
from .tui_widgets.sidebar import Sidebar, SessionInfo, ToolEventLog, RunMetrics, ShortcutHints, WorkspaceTree
from .tui_widgets.spinner_widget import SpinnerWidget
from .tui_widgets.boot_screen import BootScreen, BootInfo
from .tui_widgets.thinking_dialog import ThinkingDialog
from .tui_widgets.provider_setup import ProviderPicker, ProviderSetup
from .tui_codex_backend import CodexRunHandle
from .tui_conversation import Conversation, LiveTurn, QueuedPrompt
from .tui_widgets.queue_bar import QueueBar  # mounted inside InputPanel
from .tui_indexes import (
    FILE_INDEX_MAX_AGE, BackgroundIndex, FileList, FileSignature, SessionSummary,
    file_signature, load_session_summaries, scan_workspace_files,
)
from .tui_live_events import LiveEvent, LiveEventBatcher
from .tui_commands import MTPCommandProvider, SLASH_COMMANDS, parse_slash_command
from .tui_persistence import SessionSaver
from .tui_workers import (
    BackendSwitch, apply_backend_switch, prepare_backend_switch,
    record_turn, snapshot_tui_session, summary_for_turn, collect_prompt_attachments,
    run_prompt_blocking,
)

_ARG_SUGGESTION_PREFIX = "-> "
_SUGGESTION_SEPARATOR = " | "

# Minimum seconds between live-preview renders. Events arriving faster are
# coalesced and flushed by a trailing timer so the last chunk is never lost.
_LIVE_RENDER_INTERVAL = 0.10

# Worker groups. Textual's ``exclusive=True`` only cancels workers in the same
# group, so each conversation's runs and the codebase-memory jobs each get one.
_LLM_WORKER_GROUP = "llm"
_MEMORY_WORKER_GROUP = "memory"

_COMMAND_WORKER_GROUP = "command"

T = TypeVar("T")


class CommandOutput(RichLog):
    """Reflow saved command results after this panel receives its new size."""
    def __init__(self, reflow: Callable[[], None], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._reflow = reflow
        self._last_width = 0

    def on_resize(self, event: events.Resize) -> None:
        if event.size.width != self._last_width:
            self._last_width = event.size.width
            self.call_after_refresh(self._reflow)


class LiveEventBatch(Message):
    """Live events from one run, posted from the worker thread."""

    def __init__(self, run_id: str | None, events: list[LiveEvent]) -> None:
        super().__init__()
        self.run_id = run_id
        self.events = events


class IndexReady(Message):
    """An autocomplete index finished building. Posted from its loader thread."""


class SessionSaveFailed(Message):
    """A background session write failed. Posted from the writer thread."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error


@dataclass(slots=True)
class CodebaseScanResult:
    root: Path
    files_indexed: int
    changed_files: int
    files_deleted: int
    chunks_indexed: int
    db_path: Path


@dataclass(slots=True)
class CodebaseRefreshResult:
    root: Path
    changed_files: int
    files_deleted: int
    chunks_indexed: int


class MTPApp(App):
    """MTP Terminal UI — Async Textual Application."""

    TITLE = "MTP TUI"
    SUB_TITLE = "Model Tool Protocol"
    CSS_PATH = "tui_app.tcss"
    COMMANDS = {MTPCommandProvider}

    BINDINGS = [
        Binding("ctrl+p", "command_palette", "Commands", show=False, priority=True),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", show=False, priority=True),
        Binding("ctrl+l", "clear_chat", "Clear", show=False, priority=True),
        Binding("ctrl+d", "quit", "Quit", show=False, priority=True),
        Binding("ctrl+shift+s", "open_sandbox", "Sandbox", show=False, priority=True),
        Binding("ctrl+y", "copy_last", "Copy Output", show=True, priority=True),
        Binding("ctrl+o", "focus_output", "Read output", show=False, priority=True),
        # Only while the active conversation runs (see check_action); otherwise
        # Ctrl+X reaches the input and cuts as usual. Ctrl+C stays copy.
        Binding("ctrl+x", "interrupt", "Stop run", show=False, priority=True),
        Binding("ctrl+g", "steer_last_queued", "Steer", show=False, priority=True),
        Binding("ctrl+n", "new_conversation", "New chat", show=False, priority=True),
        Binding("alt+right,ctrl+pagedown", "next_conversation", "Next chat", show=False, priority=True),
        Binding("alt+left,ctrl+pageup", "previous_conversation", "Previous chat", show=False, priority=True),
        *(
            Binding(f"alt+{n}", f"switch_conversation({n})", f"Chat {n}", show=False, priority=True)
            for n in range(1, 10)
        ),
        *(
            Binding(f"f{n}", f"switch_conversation({n})", f"Chat {n}", show=False, priority=True)
            for n in range(1, 10)
        ),
        Binding("escape", "hide_suggestions", "Hide Suggestions", show=False),
    ]

    def __init__(self, state: TUIState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        first = Conversation(state, number=1)
        self._conversations: list[Conversation] = [first]
        self._active: Conversation = first
        self._next_number = 2
        # run_id -> conversation, for routing events from worker threads.
        self._runs: dict[str, Conversation] = {}
        self._history_index: int | None = None
        self._history_draft: str = ""
        self._pending_attachments: list[str] = []  # Track the raw prompt for recording
        self._input_history: list[str] = []
        self._codebase_scan_progress: str | None = None
        self._codebase_scan_root: Path | None = None
        self._codebase_scan_context: tuple[Worker, Conversation, Path] | None = None
        self._memory_refresh_running = False
        self._show_tool_details = False
        self._memory_refresh_queued = False
        self._memory_launch_scan_done = False
        self._backend_switch_seq = 0
        self._codex_catalog_source = "Fallback catalog; discovery pending"
        self._command_results: list[Any] = []
        self.session_saver = SessionSaver(
            on_error=lambda exc: self.post_message(SessionSaveFailed(exc)),
        )
        self._file_index: BackgroundIndex[Path, FileList] = BackgroundIndex(
            scan_workspace_files,
            on_ready=lambda: self.post_message(IndexReady()),
            max_age=FILE_INDEX_MAX_AGE,
            name="mtp-file-index",
        )
        self._session_index: BackgroundIndex[FileSignature, list[SessionSummary]] = BackgroundIndex(
            load_session_summaries,
            on_ready=lambda: self.post_message(IndexReady()),
            name="mtp-session-index",
        )

    # ── Conversations ────────────────────────────────────────────────────

    @property
    def _state(self) -> TUIState:
        """State of the conversation on screen. Commands act on this one."""
        return self._active.state

    @property
    def _pending_attachments(self) -> list[str]:
        return self._active.pending_attachments

    @_pending_attachments.setter
    def _pending_attachments(self, value: list[str]) -> None:
        self._active.pending_attachments = value

    @property
    def _input_history(self) -> list[str]:
        return self._active.input_history

    @_input_history.setter
    def _input_history(self, value: list[str]) -> None:
        self._active.input_history = value

    @property
    def state(self) -> TUIState:
        return self._active.state

    @property
    def conversations(self) -> list[Conversation]:
        return list(self._conversations)

    @property
    def active_conversation(self) -> Conversation:
        return self._active

    @property
    def active_chat_log(self) -> ChatLog:
        return self._active.view.chat_log

    def _any_running(self) -> bool:
        return any(conv.running for conv in self._conversations)

    def _conversation_for_worker(self, worker: Worker[Any]) -> Conversation | None:
        return next((conv for conv in self._conversations if conv.worker is worker), None)

    def _fresh_state(self, *, label: str | None = None) -> TUIState:
        """A new session that inherits backend and settings from the active one."""
        return dataclasses.replace(
            self._active.state,
            session_id=new_session_id(),
            session_label=label,
            transcript=[],
            last_usage_lines=[],
            agent=None,
            codex_session_id=None,
            last_tool_events=[],
            last_tool_details=[],
            last_warnings=[],
        )

    def _open_conversation(self, state: TUIState, *, activate: bool = True) -> Conversation:
        conv = Conversation(state, number=self._next_number)
        self._next_number += 1
        self._conversations.append(conv)
        self.query_one("#conversations", ContentSwitcher).mount(conv.view)
        self.query_one("#conversation-tabs", Tabs).add_tab(Tab(self._tab_label(conv), id=conv.tab_id))
        # The view composes its chat log after mounting; fill it once it exists.
        conv.view.call_after_refresh(self._rebuild_chat_log, conv)
        if activate:
            self._activate(conv)
        else:
            self._refresh_tabs()
        return conv

    def _activate(self, conv: Conversation) -> None:
        """Show ``conv``; its run, if any, keeps streaming in the background either way."""
        if conv not in self._conversations:
            return
        area = self.query_one("#chat-input", InputArea)
        previous = self._active
        if previous is not conv:
            previous.draft_text = area.text
            previous.draft_cursor = area.cursor_location
        self._active = conv
        if previous is not conv:
            area.load_text(conv.draft_text)
            area.cursor_location = conv.draft_cursor
            self._history_index = None
            self._history_draft = ""
            badges = self.query_one("#attachment-container")
            badges.remove_children()
            for filename in conv.pending_attachments:
                badges.mount(AttachmentBadge(f"📎 {filename}"))
            badges.set_class(bool(conv.pending_attachments), "visible")
            self.query_one("#suggestion-list").remove_class("visible")
        conv.unread = False
        self.query_one("#conversations", ContentSwitcher).current = conv.view.id
        tabs = self.query_one("#conversation-tabs", Tabs)
        if tabs.active != conv.tab_id:
            # add_tab is async; activating a just-added tab has to wait a frame.
            self.call_after_refresh(self._select_tab, conv)
        self._refresh_tabs()
        self._refresh_queue_bar()
        self._refresh_status_bar()
        self._refresh_sidebar()
        self._refresh_prompt_label()
        self._dismiss_command_output()
        self._focus_input()

    def _select_tab(self, conv: Conversation) -> None:
        tabs = self.query_one("#conversation-tabs", Tabs)
        if conv in self._conversations and conv is self._active and tabs.active != conv.tab_id:
            tabs.active = conv.tab_id

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None:
            return
        conv = next((c for c in self._conversations if c.tab_id == event.tab.id), None)
        if conv is not None and conv is not self._active:
            self._activate(conv)

    def _close_conversation(self, conv: Conversation) -> str:
        if conv.running:
            return f"Chat {conv.number} is still running. Stop it with Ctrl+X first."
        if len(self._conversations) == 1:
            return "This is the only open chat. Use /new to start another one."
        index = self._conversations.index(conv)
        self._conversations.remove(conv)
        self.session_saver.request(snapshot_tui_session(conv.state))
        self.query_one("#conversation-tabs", Tabs).remove_tab(conv.tab_id)
        conv.view.remove()
        if conv is self._active:
            self._activate(self._conversations[min(index, len(self._conversations) - 1)])
        else:
            self._refresh_tabs()
        return f"Closed chat {conv.number}."

    def _tab_label(self, conv: Conversation) -> Text:
        label = Text()
        position = self._conversations.index(conv) + 1 if conv in self._conversations else conv.number
        label.append(f"{position} ", style="dim")
        label.append(conv.title)
        if conv.running:
            # Static marker: Tabs re-animates its underline on every relabel,
            # so an animated spinner here would keep the UI busy nonstop.
            label.append(" ⋯", style="#38bdf8")
        elif conv.unread:
            marker, style = ("!", "bold #f43f5e") if conv.last_status == TURN_FAILED else ("●", "#34d399")
            label.append(f" {marker}", style=style)
        if conv.queue:
            label.append(f" +{len(conv.queue)}", style="#fbbf24")
        return label

    def _refresh_tabs(self) -> None:
        try:
            tabs = self.query_one("#conversation-tabs", Tabs)
        except Exception:
            return
        for conv in self._conversations:
            label = self._tab_label(conv)
            try:
                tab = tabs.query_one(f"#{conv.tab_id}", Tab)
            except Exception:
                continue  # added this frame; labelled on the next refresh
            # Relabelling re-animates the underline; skip it when nothing changed.
            if tab.label.plain != label.plain:
                tab.label = label

    def _refresh_queue_bar(self) -> None:
        try:
            bar = self.query_one("#queue-bar", QueueBar)
        except Exception:
            return
        conv = self._active
        bar.show_queue(list(conv.queue), can_steer=self._can_steer(conv))

    def _conversation_by_position(self, position: int) -> Conversation | None:
        if 1 <= position <= len(self._conversations):
            return self._conversations[position - 1]
        return None

    def action_new_conversation(self) -> None:
        conv = self._open_conversation(self._fresh_state())
        self._save_session(conv)
        self.query_one("#boot-screen", BootScreen).display = False

    def action_next_conversation(self) -> None:
        index = self._conversations.index(self._active)
        self._activate(self._conversations[(index + 1) % len(self._conversations)])

    def action_previous_conversation(self) -> None:
        index = self._conversations.index(self._active)
        self._activate(self._conversations[(index - 1) % len(self._conversations)])

    def action_switch_conversation(self, position: int) -> None:
        conv = self._conversation_by_position(int(position))
        if conv is not None:
            self._activate(conv)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self.screen_stack and isinstance(self.screen, (ProviderSetup, ProviderPicker, ThinkingDialog)):
            return False
        if action == "interrupt":
            # Without a run, let Ctrl+X fall through to the input (cut).
            return self._active.running
        if action == "steer_last_queued":
            return bool(self._active.queue) and self._can_steer(self._active)
        return True

    # ── Compose ──────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        first = self._conversations[0]
        with Horizontal():
            with Vertical(id="main-container"):
                yield Tabs(Tab(self._tab_label(first), id=first.tab_id), id="conversation-tabs")
                yield BootScreen(id="boot-screen")
                with ContentSwitcher(id="conversations", initial=first.view.id):
                    yield first.view
                # App-level jobs (codebase indexing); run spinners live in each view.
                yield SpinnerWidget(id="task-spinner")
                yield CommandOutput(self._render_command_results, id="cmd-log", min_width=1, markup=False, highlight=False, wrap=True)
                # InputPanel is docked to the bottom and holds the queue bar,
                # so queued messages sit right above where you type.
                yield InputPanel(id="input-panel")
            yield Sidebar(id="sidebar")
        yield StatusBar(id="status-bar")

    # ── Mount ────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._refresh_status_bar()
        self._refresh_sidebar()
        self._refresh_prompt_label()
        self._rebuild_chat_log(self._active)
        self._refresh_queue_bar()
        self._show_boot_info()
        self.query_one("#main-container").set_class(not self._state.transcript, "home-view")
        self.query_one("#boot-screen").display = not self._state.transcript
        # Typing works as soon as the first frame is up; no fixed delay.
        self.call_after_refresh(self._focus_input)
        if self._state.codex_bin:
            self.call_after_refresh(self._refresh_codex_models)
        # Memory status touches sqlite; start it after the first paint.
        self.call_after_refresh(
            lambda: self._request_background_memory_refresh(reason="startup", prefer_full_scan=True)
        )
        self.call_after_refresh(self._offer_initial_provider_setup)

    def _offer_initial_provider_setup(self) -> None:
        from .tui_settings import is_provider_configured, load_provider_settings, provider_settings_path
        from .tui_provider_factory import SUPPORTED_TUI_PROVIDERS
        state = self._state
        if state.backend in SUPPORTED_TUI_PROVIDERS and not is_provider_configured(
            load_provider_settings(provider_settings_path(state.session_store.file_path)), state.backend,
        ):
            self._open_provider_setup(state.backend, switching=True)

    def _open_provider_picker(self, *, managing_keys: bool = False) -> None:
        from .tui_settings import provider_settings_path
        owner = self._active
        def selected(provider: str | None) -> None:
            if not provider:
                self._focus_input()
            elif provider == "codex":
                if managing_keys:
                    self._write_cmd_log("Codex uses its CLI login, not a provider API key. Use /codex login or choose another provider with /apikey.")
                else:
                    self._start_backend_switch(provider, self)
            elif managing_keys:
                self.call_later(lambda: self._open_provider_setup(provider, owner=owner))
            else:
                self._start_backend_switch(provider, self)
        self.push_screen(ProviderPicker(provider_settings_path(owner.state.session_store.file_path), owner.state.backend, managing_keys=managing_keys), selected)

    def add_command_result(self, content: Any) -> None:
        """The provider selector uses the same command feedback as slash commands."""
        self._write_cmd_log(content)

    def _open_provider_setup(self, provider: str, *, switching: bool = False, owner: Conversation | None = None) -> None:
        from .tui_settings import provider_settings_path
        from .tui_provider_factory import SUPPORTED_TUI_PROVIDERS
        if provider not in SUPPORTED_TUI_PROVIDERS:
            self._write_cmd_log(f"Unknown provider: {provider}. Use /backend to choose one.")
            return
        owner = owner or self._active
        def finished(result: str | None) -> None:
            if result == "change":
                self.call_later(lambda: self._open_provider_picker(managing_keys=not switching))
                return
            if result in {"saved", "deleted"}:
                # Credentials are shared, but agents and backend selection belong to chats.
                for conv in self._conversations:
                    if conv.state.backend == provider:
                        conv.state.agent = None
                if result == "saved" and switching and owner in self._conversations:
                    self._start_backend_switch(provider, self)
                else:
                    self._write_cmd_log(f"{provider}: {'settings saved' if result == 'saved' else 'saved key removed'}. Credentials have not been checked with the provider.")
                self._refresh_status_bar()
                self._refresh_sidebar()
            self._focus_input()
        self.push_screen(ProviderSetup(provider, provider_settings_path(owner.state.session_store.file_path), switching=switching), finished)

    def _needs_provider_setup(self, conv: Conversation) -> bool:
        from .tui_settings import is_provider_configured, load_provider_settings, provider_settings_path
        return conv.state.backend != "codex" and not is_provider_configured(
            load_provider_settings(provider_settings_path(conv.state.session_store.file_path)), conv.state.backend,
        )

    def _save_session(self, conv: Conversation | None = None) -> None:
        """Snapshot state now; the write happens debounced on a background thread."""
        self.session_saver.request(snapshot_tui_session((conv or self._active).state))

    def on_session_save_failed(self, message: SessionSaveFailed) -> None:
        self.notify(f"Could not save session: {message.error}", title="Save failed", severity="error")

    def on_unmount(self) -> None:
        # Ask every running chat to stop so worker threads and Codex
        # subprocesses do not outlive the app, then save what is queued.
        for conv in self._conversations:
            conv.queue.clear()
            self._request_interrupt(conv)
        self.session_saver.close()

    def _focus_input(self) -> None:
        try:
            self.query_one("#chat-input", InputArea).focus()
        except Exception:
            pass

    def _show_boot_info(self) -> None:
        try:
            from mtp import __version__
        except Exception:
            __version__ = "0.0.0"
        model = active_model_name(self._state)
        sid = self._state.session_id.split("-")[-1][:8]
        cwd = str(self._state.cwd.name or self._state.cwd)
        thinking = get_thinking_capability(self._state)
        boot_info = self.query_one("#boot-info", BootInfo)
        boot_info.set_info(
            version=__version__, backend=self._state.backend,
            model=model, session_short=sid, cwd=cwd,
            thinking_label=thinking.label if thinking else None,
            thinking_value=thinking.current_label if thinking else None,
        )

    # ── UI refresh helpers ───────────────────────────────────────────────

    @staticmethod
    def _turn_banner(conv: Conversation, live: LiveTurn) -> str:
        return f"> {live.backend} - {live.model_name} - mode={conv.state.harness_mode}"

    def _rebuild_chat_log(self, conv: Conversation | None = None) -> None:
        conv = conv or self._active
        if conv not in self._conversations:
            return
        chat_log = conv.view.chat_log
        chat_log.load_history([
            HistoryTurn(prompt=turn.prompt, attachments=list(turn.attachments), reply=self._turn_message(turn))
            for turn in conv.state.transcript
        ])
        if conv.live is not None:
            self._mount_pending_turn(conv)
            if conv.live.has_output():
                chat_log.set_live_assistant_message(conv.live.message(show_tool_details=self._show_tool_details))

    def _turn_message(self, turn: TranscriptTurn) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            text=turn.response,
            model=turn.model,
            backend=turn.backend,
            warnings=turn.warnings,
            usage_lines=turn.usage_lines,
            thinking=turn.thinking_text,
            tool_details=list(turn.tool_details),
            show_tool_details=self._show_tool_details,
            assistant_blocks=list(turn.assistant_blocks),
            collapse_thinking=True,
            status=turn.status,
            error=turn.error,
        )

    def _mount_pending_turn(self, conv: Conversation) -> None:
        """Show the prompt being run plus its banner, without a full rebuild."""
        live = conv.live
        if live is None:
            return
        chat_log = conv.view.chat_log
        live.widgets = [
            chat_log.add_user_message(live.display_prompt, live.display_attachments),
            chat_log.add_system_message(self._turn_banner(conv, live)),
        ]

    def _complete_pending_turn(self, conv: Conversation, turn: TranscriptTurn) -> None:
        """Replace the live preview with the finished turn in place."""
        if conv.live is not None:
            # The banner only describes an in-flight run; saved turns don't show it.
            for widget in conv.live.widgets[1:]:
                widget.remove()
            conv.live.widgets = []
        conv.view.chat_log.finalize_live_assistant_message(self._turn_message(turn))

    def _report_settings_recoveries(self) -> None:
        for recovery in pop_settings_recoveries():
            self.notify(recovery.message(), title="Settings file was corrupt", severity="warning", timeout=20)

    def _refresh_status_bar(self) -> None:
        self._report_settings_recoveries()
        try:
            thinking = get_thinking_capability(self._state)
            self.query_one("#status-bar", StatusBar).update_status(
                backend=self._state.backend,
                model=active_model_name(self._state),
                session_id=self._state.session_id,
                mode=self._state.harness_mode,
                turn_count=len(self._state.transcript),
                sandbox_mode=self._state.codex_sandbox_mode,
                thinking_label=thinking.label if thinking else None,
                thinking_value=thinking.current_label if thinking else None,
                is_running=self._active.running,
                needs_setup=self._needs_provider_setup(self._active),
            )
        except Exception:
            pass
        self._show_boot_info()

    def _refresh_prompt_label(self) -> None:
        try:
            label = self.query_one("#prompt-label", PromptLabel)
            cwd_name = self._state.cwd.name or str(self._state.cwd)
            sid_short = self._state.session_id.split("-")[-1][:6]
            label.update_label(cwd_name, self._state.backend, sid_short)
        except Exception:
            pass

    def _refresh_sidebar(self) -> None:
        try:
            thinking = get_thinking_capability(self._state)
            self.query_one("#session-info", SessionInfo).update_info(
                session_id=self._state.session_id,
                label=self._state.session_label or "",
                backend=self._state.backend,
                model=active_model_name(self._state),
                turn_count=len(self._state.transcript),
                mode=self._state.harness_mode,
                thinking_label=thinking.label if thinking else None,
                thinking_value=thinking.current_label if thinking else None,
            )
            self.query_one("#tool-event-log", ToolEventLog).update_events(
                self._state.last_tool_events
            )
            self.query_one("#run-metrics", RunMetrics).update_metrics(self._state.last_usage_lines)
            self.query_one("#shortcut-hints", ShortcutHints).update_hints()
            self.query_one("#workspace-tree", WorkspaceTree).refresh_tree(self._state.cwd)
        except Exception:
            pass

    def on_thinking_badge_activated(self, event: ThinkingBadge.Activated) -> None:
        capability = get_thinking_capability(self._state)
        if capability is None:
            return

        def _apply(result: str | None) -> None:
            if not result:
                return
            try:
                message = apply_thinking_value(self._state, result, persist=False)
                self._save_session()
            except ValueError as exc:
                self.notify(str(exc), title="Thinking", severity="error")
                return
            self._refresh_status_bar()
            self._refresh_sidebar()
            self.active_chat_log.add_command_result(message)

        self.push_screen(ThinkingDialog(capability), callback=_apply)

    # ── Input handling ───────────────────────────────────────────────────

    def on_input_area_submitted(self, event: InputArea.Submitted) -> None:
        raw = event.value.strip()
        self._history_index = None
        self._history_draft = ""
        
        history_value = raw
        if re.match(r"/apikey\s+set\s+\S+\s+", raw, re.IGNORECASE):
            history_value = "/apikey " + raw.split(None, 3)[2]
        if history_value and (not self._input_history or self._input_history[-1] != history_value):
            self._input_history.append(history_value)
            
        try:
            cmd_log = self.query_one("#cmd-log", RichLog)
            cmd_log.remove_class("visible")
        except Exception:
            pass
        
        # Check if it's a command before applying attachments
        parsed = parse_slash_command(raw)
        if parsed is not None:
            cmd, arg = parsed
            self._dispatch_command(cmd, arg)
            # We return early. Attachments remain pending for the next actual prompt.
            return

        if raw and self._needs_provider_setup(self._active):
            self.query_one("#chat-input", InputArea).text = raw
            self._open_provider_setup(self._state.backend, switching=True)
            return
        
        if self._pending_attachments:
            raw = " ".join([f'@"{att}"' for att in self._pending_attachments]) + " " + raw
            self._pending_attachments.clear()
            container = self.query_one("#attachment-container")
            for child in container.children:
                child.remove()
            container.remove_class("visible")
            
        raw = raw.strip()
        if not raw:
            return

        self._send_prompt(raw)



    # ── History & Autocomplete ──────────────────────────────────────────────


    def on_input_area_remove_last_attachment(self, event) -> None:
        if hasattr(self, "_pending_attachments") and self._pending_attachments:
            self._pending_attachments.pop()
            container = self.query_one("#attachment-container")
            if container.children:
                container.children[-1].remove()
            if not self._pending_attachments:
                container.remove_class("visible")

    def on_attachment_badge_remove_attachment(self, event) -> None:
        if hasattr(self, "_pending_attachments") and event.filename in self._pending_attachments:
            self._pending_attachments.remove(event.filename)
            if not self._pending_attachments:
                self.query_one("#attachment-container").remove_class("visible")

    def _request_interrupt(self, conv: Conversation | None = None) -> bool:
        """Ask a conversation's run to stop. Returns True if a cancel was issued."""
        conv = conv or self._active
        if not conv.running or conv.run_id is None:
            return False
        if conv.codex_handle is not None:
            return conv.codex_handle.cancel()
        agent = conv.state.agent
        if agent is None:
            return False
        return bool(agent.cancel_run(conv.run_id))

    def action_interrupt(self) -> None:
        conv = self._active
        if self._request_interrupt(conv):
            conv.view.spinner.update_label("Stopping")
            conv.view.chat_log.add_system_message("  Interrupt requested...", style="#fbbf24")

    def action_hide_suggestions(self) -> None:
        # Esc closes an open suggestion list first; only then does it stop a run.
        option_list = self.query_one("#suggestion-list", OptionList)
        if option_list.has_class("visible"):
            option_list.remove_class("visible")
            self.query_one("#chat-input", InputArea).focus()
            return
        if self.query_one("#main-container").has_class("command-view"):
            self._dismiss_command_output()
            return
        if self._active.running:
            self.action_interrupt()

    def on_input_area_history_navigate(self, event: InputArea.HistoryNavigate) -> None:
        turns = self._input_history
        if not turns:
            return
        
        input_area = self.query_one("#chat-input", InputArea)
        
        if self._history_index is None:
            if event.direction == -1:
                self._history_index = len(turns) - 1
                self._history_draft = input_area.text
            else:
                return
        else:
            self._history_index += event.direction
            
        if self._history_index < 0:
            self._history_index = 0
        elif self._history_index >= len(turns):
            self._history_index = None
            input_area.text = self._history_draft
            # Textual TextArea cursor location requires row, col
            lines = input_area.text.split("\n")
            input_area.cursor_location = (len(lines) - 1, len(lines[-1]))
            return
            
        input_area.text = turns[self._history_index]
        if event.direction == -1:
            input_area.cursor_location = (0, 0)
        else:
            lines = input_area.text.split("\n")
            input_area.cursor_location = (len(lines) - 1, len(lines[-1]))

    def on_text_area_changed(self, event: InputArea.Changed) -> None:
        # TextArea.Changed dispatches to on_text_area_changed, even from the
        # InputArea subclass; the old on_input_area_changed name never ran.
        if isinstance(event.text_area, InputArea):
            self._update_suggestions(event.text_area)

    def on_index_ready(self, message: IndexReady) -> None:
        """An autocomplete index finished building; refresh suggestions that use it."""
        input_area = self.query_one("#chat-input", InputArea)
        row, col = input_area.cursor_location
        lines = input_area.text.split("\n")
        if row >= len(lines):
            return
        line = lines[row][:col]
        words = line.split()
        wants_files = bool(words) and words[-1].startswith("@") and line.endswith(words[-1])
        wants_sessions = line.split(" ", 1)[0].lower() in {"/load", "/open", "/sessions"}
        if wants_files or wants_sessions:
            self._update_suggestions(input_area)

    def _update_suggestions(self, input_area: InputArea) -> None:
        cursor_row, cursor_col = input_area.cursor_location
        lines = input_area.text.split("\n")
        if cursor_row >= len(lines):
            return
            
        current_line = lines[cursor_row][:cursor_col]
        words = current_line.split()
        
        # Check for command arguments
        if current_line.startswith("/"):
            parts = current_line.split()
            if len(parts) > 1 or (len(parts) == 1 and current_line.endswith(" ")):
                cmd = parts[0][1:].lower()
                partial = current_line.split(maxsplit=1)[1].lower() if len(parts) > 1 else ""
                if len(parts) <= 2 or cmd == "codebase":
                    self._show_command_argument_suggestions(cmd, partial)
                else:
                    try: self.query_one("#suggestion-list", OptionList).remove_class("visible")
                    except Exception: pass
                return
            else:
                cmd_partial = parts[0] if parts else ""
                self._show_command_suggestions(cmd_partial)
                return

        if not words or not current_line.endswith(words[-1]):
            try:
                self.query_one("#suggestion-list", OptionList).remove_class("visible")
            except Exception: pass
            return
            
        last_word = words[-1]
        
        if last_word.startswith("@"):
            self._show_file_suggestions(last_word[1:])
        elif len(words) == 1 and len(last_word) >= 1:
            matches = [h for h in self._input_history if h.startswith(last_word) and h != last_word]
            matches = list(dict.fromkeys(matches))[:10]
            if matches:
                self._populate_and_show_suggestions(matches, prefix="")
            else:
                try: self.query_one("#suggestion-list", OptionList).remove_class("visible")
                except Exception: pass
        else:
            try: self.query_one("#suggestion-list", OptionList).remove_class("visible")
            except Exception: pass

    def on_input_area_tab_pressed(self, event: InputArea.TabPressed) -> None:
        input_area = self.query_one("#chat-input", InputArea)
        cursor_row, cursor_col = input_area.cursor_location
        lines = input_area.text.split("\n")
        current_line = lines[cursor_row][:cursor_col]
        words = current_line.split()
        if not words:
            return
            
        last_word = words[-1]
        if last_word.startswith("@"):
            partial = last_word[1:]
            self._show_file_suggestions(partial)
        elif last_word.startswith("/"):
            partial = last_word
            self._show_command_suggestions(partial)

    def _show_file_suggestions(self, partial: str) -> None:
        # Served from memory; while the first build runs this is empty and
        # on_index_ready fills it in.
        files = self._file_index.get(self._state.cwd)
        matches = files.matching(partial) if files is not None else []
        if not matches:
            try: self.query_one("#suggestion-list", OptionList).remove_class("visible")
            except Exception: pass
            return
            
        self._populate_and_show_suggestions(matches, prefix="@")

    def _show_command_suggestions(self, partial: str) -> None:
        cmd_desc = {
            "/help": "Show this reference",
            "/exit": "Quit TUI",
            "/clear": "Clear chat log",
            "/status": "Show session state",
            "/sessions": "List saved sessions",
            "/history": "Show recent turns",
            "/tools": "Show last tool events",
            "/details": "Toggle expanded tool metadata",
            "/backend": "Switch provider",
            "/model": "Switch model",
            "/models": "Show all models",
            "/apikey": "Manage API keys",
            "/thinking": "Set thinking mode",
            "/mode": "Set harness mode",
            "/sandbox": "Cycle sandbox mode",
            "/load": "Load a session",
            "/open": "Open a session",
            "/new": "Open a new chat (Ctrl+N)",
            "/reset": "Open a new chat",
            "/tabs": "List open chats",
            "/switch": "Switch chat (F1..F9 or Alt+Left/Right)",
            "/close": "Close a chat",
            "/queue": "Show or clear queued messages",
            "/steer": "Add a message to the running reply",
            "/rounds": "Set max rounds",
            "/cd": "Change directory",
            "/autoresearch": "Toggle auto-research",
            "/research": "Set research instructions",
            "/codebase": "Codebase memory",
            "/codex": "Codex auth and diagnostics",
        }
        
        matches = []
        for cmd, desc in cmd_desc.items():
            if cmd.startswith(partial.lower()):
                matches.append(f"{cmd}{_SUGGESTION_SEPARATOR}{desc}")
                
        if not matches:
            try: self.query_one("#suggestion-list", OptionList).remove_class("visible")
            except Exception: pass
            return
        self._populate_and_show_suggestions(matches, prefix="")

    def _populate_and_show_suggestions(self, matches: list[str], prefix: str) -> None:
        option_list = self.query_one("#suggestion-list", OptionList)
        option_list.clear_options()
        for m in matches:
            option_list.add_option(f"{prefix}{m}")
        
        option_list.add_class("visible")

    def _show_command_argument_suggestions(self, cmd: str, partial: str) -> None:
        matches = []
        if cmd == "backend":
            from .tui_provider_factory import SUPPORTED_TUI_PROVIDERS
            options = ["codex"] + sorted(SUPPORTED_TUI_PROVIDERS)
            matches = [p for p in options if partial in p.lower()]
        elif cmd == "model":
            options = self._model_choices()
            matches = [m for m in options if partial in m.lower()]
        elif cmd in ("load", "sessions", "open"):
            signature = file_signature(self._state.session_store.file_path)
            summaries = (self._session_index.get(signature) if signature is not None else None) or []
            for summary in summaries:
                if partial in summary.search_text():
                    display = summary.short_id
                    if summary.label:
                        display += f"{_SUGGESTION_SEPARATOR}{summary.label}"
                    display += f"  ({summary.turns} turns)"
                    matches.append(display)
        elif cmd == "mode":
            from .tui_harness_policy import HARNESS_MODES
            matches = [m for m in HARNESS_MODES if partial in m.lower()]
        elif cmd in {"reasoning", "thinking"}:
            capability = get_thinking_capability(self._state)
            matches = [option.label for option in capability.options if partial in option.label.lower()] if capability else []
        elif cmd == "details":
            options = ["toggle", "on", "off"]
            matches = [m for m in options if partial in m.lower()]
        elif cmd == "sandbox":
            options = ["read-only", "workspace-write", "danger-full-access"]
            matches = [m for m in options if partial in m.lower()]
        elif cmd == "codebase":
            normalized = partial.strip().lower()
            if not normalized:
                matches = ["memory", "status"]
            elif normalized == "memory":
                matches = ["on", "off", "show"]
            elif normalized.startswith("memory "):
                tail = normalized.split(" ", 1)[1]
                matches = [item for item in ["on", "off", "show"] if item.startswith(tail)]
            else:
                matches = [m for m in ["memory", "status"] if m.startswith(normalized)]
        elif cmd == "codex":
            normalized = partial.strip().lower()
            options = ["login", "logout", "status", "account", "models", "doctor", "repair-config"]
            matches = [item for item in options if item.startswith(normalized)]
            
        if not matches:
            try: self.query_one("#suggestion-list", OptionList).remove_class("visible")
            except Exception: pass
            return
            
        self._populate_and_show_suggestions(matches, prefix=_ARG_SUGGESTION_PREFIX)

    @staticmethod
    def _command_supports_arguments(cmd: str) -> bool:
        return cmd in {
            "backend", "model", "load", "sessions", "open", "mode",
            "reasoning", "thinking", "details", "sandbox", "codebase", "codex",
        }

    def _show_next_command_suggestions(self, input_area: InputArea) -> None:
        cursor_row, cursor_col = input_area.cursor_location
        lines = input_area.text.split("\n")
        current_line = lines[cursor_row][:cursor_col]
        if not current_line.startswith("/"):
            return
        parts = current_line.split()
        if not parts:
            self._show_command_suggestions("/")
            return
        cmd = parts[0][1:].lower()
        if len(parts) == 1 and current_line.endswith(" ") and self._command_supports_arguments(cmd):
            self._show_command_argument_suggestions(cmd, "")
            return
        if cmd == "codebase":
            if len(parts) == 2 and parts[1].lower() == "memory" and current_line.endswith(" "):
                self._show_command_argument_suggestions(cmd, "memory ")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_list = event.option_list
        if option_list.id != "suggestion-list":
            return
        event.stop()
        option_list.remove_class("visible")
        selected = str(event.option.prompt)
        
        input_area = self.query_one("#chat-input", InputArea)
        input_area.focus()
        
        cursor_row, cursor_col = input_area.cursor_location
        lines = input_area.text.split("\n")
        current_line = lines[cursor_row][:cursor_col]
        words = current_line.split()
        
        if selected.startswith(_ARG_SUGGESTION_PREFIX):
            val = selected[len(_ARG_SUGGESTION_PREFIX):].split()[0]
            parts = current_line.split()
            if not parts:
                return
            cmd = parts[0][1:].lower()
            needs_more = cmd == "codebase" and val == "memory"
            # Replace the current argument. Only codebase memory has a second
            # picker slot; a completed single argument must never be appended.
            base = ("/load" if cmd == "sessions" else parts[0]) + " "
            if cmd == "codebase" and len(parts) >= 2 and parts[1].lower() == "memory" and val != "memory":
                base += "memory "
            new_line = base + val
            if needs_more:
                new_line += " "
            lines[cursor_row] = new_line + lines[cursor_row][cursor_col:]
            input_area.text = "\n".join(lines)
            input_area.cursor_location = (cursor_row, len(new_line))
            if needs_more:
                self._show_next_command_suggestions(input_area)
            else:
                self._submit_completed_command(input_area)
            return

        if not words:
            return
            
        last_word = words[-1]
        
        if selected.startswith("@"):
            start_idx = current_line.rfind(last_word)
            new_line = current_line[:start_idx]
            lines[cursor_row] = new_line + lines[cursor_row][cursor_col:]
            input_area.text = "\n".join(lines)
            input_area.cursor_location = (cursor_row, start_idx)
            
            filename = selected[1:]
            if filename not in self._pending_attachments:
                self._pending_attachments.append(filename)
                from .tui_widgets.input_area import AttachmentBadge
                container = self.query_one("#attachment-container")
                container.mount(AttachmentBadge(f"📎 {filename}"))
                container.add_class("visible")
        else:
            if _SUGGESTION_SEPARATOR in selected:
                selected = selected.split(_SUGGESTION_SEPARATOR)[0]
            start_idx = current_line.rfind(last_word)
            new_line = current_line[:start_idx] + selected + " "
            lines[cursor_row] = new_line + lines[cursor_row][cursor_col:]
            input_area.text = "\n".join(lines)
            input_area.cursor_location = (cursor_row, start_idx + len(selected) + 1)
            self._show_next_command_suggestions(input_area)
            if selected.startswith("/") and selected in SLASH_COMMANDS:
                self._submit_completed_command(input_area)

    def _submit_completed_command(self, input_area: InputArea) -> None:
        value = input_area.text.strip()
        input_area.text = ""
        self.query_one("#suggestion-list", OptionList).remove_class("visible")
        input_area.post_message(InputArea.Submitted(value))

    def _send_prompt(self, raw: str) -> None:
        """Run ``raw`` in the active conversation, or queue it if that one is running."""
        conv = self._active
        if conv.running:
            self._enqueue(conv, raw)
            return
        self._start_run(conv, raw)

    # ── Queue and steering ───────────────────────────────────────────────

    @staticmethod
    def _can_steer(conv: Conversation) -> bool:
        """Steering needs an MTP agent mid-run; Codex exec cannot take input."""
        agent = conv.state.agent
        return (
            conv.running
            and conv.state.backend != "codex"
            and agent is not None
            and callable(getattr(agent, "steer_run", None))
        )

    def _enqueue(self, conv: Conversation, raw: str, *, front: bool = False, note: str = "") -> None:
        item = QueuedPrompt(raw)
        if front:
            conv.queue.appendleft(item)
        else:
            conv.queue.append(item)
        if conv is self._active and not note:
            hint = (
                "Ctrl+G sends it into the current run instead."
                if self._can_steer(conv)
                else "It runs when the current reply finishes."
            )
            self.notify(f"Queued ({len(conv.queue)}). {hint}", title=f"Chat {self._position(conv)}", timeout=4)
        elif note:
            self.notify(note, title=f"Chat {self._position(conv)}", timeout=6)
        self._refresh_queue_bar()
        self._refresh_tabs()

    def _position(self, conv: Conversation) -> int:
        return self._conversations.index(conv) + 1 if conv in self._conversations else conv.number

    def _steer(self, conv: Conversation, text: str) -> bool:
        """Hand ``text`` to the running agent. Returns False if it cannot take it."""
        agent = conv.state.agent
        if not self._can_steer(conv) or conv.run_id is None or not agent.steer_run(conv.run_id, text):
            return False
        if conv.live is not None:
            conv.live.blocks.append({"type": "steer", "text": text})
            conv.live.steered.append(text)
            self._render_live_preview(conv, force=True)
        return True

    def _steer_queued(self, conv: Conversation, item: QueuedPrompt) -> None:
        if self._steer(conv, item.text):
            try:
                conv.queue.remove(item)
            except ValueError:
                pass
        else:
            self.notify("This run cannot take new input now; the message stays queued.", severity="warning")
        self._refresh_queue_bar()
        self._refresh_tabs()

    def action_steer_last_queued(self) -> None:
        conv = self._active
        if conv.queue:
            self._steer_queued(conv, conv.queue[-1])

    def action_steer_queued(self, item_id: str) -> None:
        conv = self._active
        item = next((q for q in conv.queue if q.id == item_id), None)
        if item is not None:
            self._steer_queued(conv, item)

    def action_drop_queued(self, item_id: str) -> None:
        conv = self._active
        item = next((q for q in conv.queue if q.id == item_id), None)
        if item is not None:
            conv.queue.remove(item)
            self._refresh_queue_bar()
            self._refresh_tabs()

    def _run_next_queued(self, conv: Conversation) -> None:
        if conv.running or not conv.queue or conv not in self._conversations:
            return
        if self._needs_provider_setup(conv):
            if conv is self._active:
                self._open_provider_setup(conv.state.backend, switching=True)
            return
        item = conv.queue.popleft()
        self._refresh_queue_bar()
        self._start_run(conv, item.text)

    # ── Runs ─────────────────────────────────────────────────────────────

    def _start_run(self, conv: Conversation, raw: str) -> None:
        if self._needs_provider_setup(conv):
            if conv is self._active:
                self.query_one("#chat-input", InputArea).text = raw
                self._open_provider_setup(conv.state.backend, switching=True)
            return
        self.query_one("#boot-screen", BootScreen).display = False
        self.query_one("#main-container").remove_class("home-view", "command-view")
        if conv is self._active:
            cmd_log = self.query_one("#cmd-log", RichLog)
            cmd_log.clear()
            cmd_log.remove_class("visible")

        expanded, attachments, att_warnings = collect_prompt_attachments(raw, conv.state.cwd)

        self._reset_live_preview(conv)
        conv.live = LiveTurn(
            raw_prompt=raw,
            display_prompt=raw,
            display_attachments=list(attachments),
            backend=conv.state.backend,
            # Resolved once per run; live frames must not read settings from disk.
            model_name=active_model_name(conv.state),
        )
        self._mount_pending_turn(conv)
        conv.view.spinner.start("Thinking")

        conv.run_id = f"run-{uuid4().hex[:12]}"
        self._runs[conv.run_id] = conv
        conv.codex_handle = CodexRunHandle() if conv.state.backend == "codex" else None
        conv.worker = self.run_worker(
            self._run_llm_worker(conv, expanded, attachments, att_warnings),
            name="llm_call",
            # One group per conversation, so exclusive never cancels another chat.
            group=f"{_LLM_WORKER_GROUP}-{conv.id}",
            exclusive=True,
            # A crash is recorded as a failed turn; it must not quit the app.
            exit_on_error=False,
        )
        self._refresh_tabs()
        self._refresh_status_bar()

    async def _run_llm_worker(
        self, conv: Conversation, expanded_prompt: str, attachments: list[str], att_warnings: list[str],
    ) -> ChatResult:
        """Worker coroutine — runs blocking LLM call in thread."""
        import asyncio

        run_id = conv.run_id
        codex_handle = conv.codex_handle
        # post_message is thread-safe and non-blocking, so the model stream
        # never waits on the UI; the batcher merges token chunks.
        batcher = LiveEventBatcher(lambda batch: self.post_message(LiveEventBatch(run_id, batch)))
        state = conv.state

        def run() -> ChatResult:
            try:
                return run_prompt_blocking(
                    state,
                    expanded_prompt,
                    emit_callback=batcher.emit,
                    run_id=run_id,
                    codex_handle=codex_handle,
                )
            finally:
                # Flushed before the worker result, so the UI sees every chunk first.
                batcher.close()

        result = await asyncio.to_thread(run)
        result.attachments = attachments
        result.warnings = [*att_warnings, *result.warnings]
        return result

    async def _run_codebase_scan_worker(self, root: Path) -> CodebaseScanResult:
        import asyncio
        from mtp.codebase import CodebaseMemory

        memory = CodebaseMemory(root)

        def progress(stats) -> None:
            self.call_from_thread(self._update_codebase_scan_progress, stats.percent, stats.files_seen, stats.changed_files)

        stats = await asyncio.to_thread(memory.scan, enable=True, progress=progress)
        return CodebaseScanResult(
            root=root,
            files_indexed=stats.files_indexed,
            changed_files=stats.changed_files,
            files_deleted=stats.files_deleted,
            chunks_indexed=stats.chunks_indexed,
            db_path=memory.db_path,
        )

    async def _run_codebase_refresh_worker(self, root: Path) -> CodebaseRefreshResult:
        import asyncio
        from mtp.codebase import CodebaseMemory

        memory = CodebaseMemory(root)
        stats = await asyncio.to_thread(memory.refresh_changed)
        return CodebaseRefreshResult(
            root=root,
            changed_files=stats.changed_files,
            files_deleted=stats.files_deleted,
            chunks_indexed=stats.chunks_indexed,
        )

    def _update_codebase_scan_progress(self, percent: int, files_seen: int, changed_files: int) -> None:
        self._codebase_scan_progress = f"Indexing codebase {percent}%  files={files_seen} changed={changed_files}"
        self.query_one("#task-spinner", SpinnerWidget).update_label(self._codebase_scan_progress)

    def _append_cmd_log(self, message: str, *, style: str = "#a78bfa") -> None:
        from rich.text import Text

        cmd_log = self.query_one("#cmd-log", RichLog)
        cmd_log.add_class("visible")
        cmd_log.write(Text(f"  {message}", style=style))

    def _request_background_memory_refresh(self, *, reason: str, prefer_full_scan: bool = False) -> None:
        try:
            from mtp.codebase import CodebaseMemory

            memory = CodebaseMemory(self._state.cwd)
            status = memory.status()
            if not status.enabled:
                self._memory_refresh_queued = False
                return
        except Exception:
            return

        if self._any_running() or self._memory_refresh_running or self._codebase_scan_progress is not None:
            self._memory_refresh_queued = True
            return

        should_full_scan = prefer_full_scan or not status.last_scan_at or status.file_count == 0 or status.chunk_count == 0
        if should_full_scan:
            self._memory_launch_scan_done = True
            self._show_scan_spinner("Indexing codebase 0%")
            self._codebase_scan_root = self._state.cwd
            self._codebase_scan_progress = "Indexing codebase 0%"
            self._launch_codebase_scan(self._state.cwd)
            return

        self._memory_refresh_running = True
        self.run_worker(
            self._run_codebase_refresh_worker(self._state.cwd),
            name="codebase_refresh",
            group=_MEMORY_WORKER_GROUP, exit_on_error=False,
            exclusive=False,
        )

    def _format_codebase_memory_show(self, root: Path) -> str:
        from mtp.codebase import CodebaseMemory

        data = CodebaseMemory(root).show(limit=8)
        lines = [
            f"Codebase memory {'ON' if data['enabled'] else 'OFF'}",
            f"root={data['root']}",
            f"db={data['db_path']}",
            f"db_size_bytes={data['db_size_bytes']}",
            f"files={data['files']} chunks={data['chunks']} summaries={data['summaries']}",
            f"last_scan_at={data['last_scan_at'] or '(never)'}",
        ]
        if data["languages"]:
            lines.append("languages=" + ", ".join(f"{item['language']}:{item['files']}" for item in data["languages"]))
        if data["chunk_kinds"]:
            lines.append("chunk_kinds=" + ", ".join(f"{item['kind']}:{item['count']}" for item in data["chunk_kinds"]))
        if data["largest_files"]:
            lines.append("largest_files=")
            for item in data["largest_files"]:
                lines.append(f"  {item['path']} size={item['size']} lines={item['lines']} lang={item['language']}")
        if data["recent_summaries"]:
            lines.append("recent_summaries=")
            for item in data["recent_summaries"]:
                model = f" model={item['model']}" if item["model"] else ""
                lines.append(f"  {item['created_at']} {item['title']}{model}")
        return "\n".join(lines)

    @staticmethod
    def _tool_mutates_workspace(tool_name: str) -> bool:
        return tool_name.startswith(("edit.", "shell.", "test."))

    def _reset_live_preview(self, conv: Conversation) -> None:
        live = conv.live
        if live is not None and live.flush_timer is not None:
            live.flush_timer.stop()
        conv.live = None
        conv.view.chat_log.clear_live_assistant_message()

    def _render_live_preview(self, conv: Conversation, *, force: bool = False) -> None:
        live = conv.live
        if live is None:
            return
        now = time.monotonic()
        wait = _LIVE_RENDER_INTERVAL - (now - live.last_render_at)
        if not force and wait > 0:
            # Throttled: make sure a render still happens once the window ends.
            if live.flush_timer is None:
                live.flush_timer = self.set_timer(wait, lambda: self._flush_live_preview(conv, live))
            return
        if live.flush_timer is not None:
            live.flush_timer.stop()
            live.flush_timer = None
        live.last_render_at = now
        conv.view.chat_log.set_live_assistant_message(live.message(show_tool_details=self._show_tool_details))

    def _flush_live_preview(self, conv: Conversation, live: LiveTurn) -> None:
        live.flush_timer = None
        if conv.live is live:
            self._render_live_preview(conv, force=True)

    def on_live_event_batch(self, message: LiveEventBatch) -> None:
        """Apply a batch of live events to the conversation whose run sent it.

        Worker threads outlive cancellation of their asyncio wrapper, so a
        finished run can still emit; those events are dropped.
        """
        conv = self._runs.get(message.run_id or "")
        if conv is None or conv.run_id != message.run_id or conv.live is None:
            return
        for kind, payload in message.events:
            self._handle_live_event(conv, kind, payload)
        self._render_live_preview(conv)

    def _handle_live_event(self, conv: Conversation, kind: str, message: Any) -> None:
        live = conv.live
        if live is None:
            return
        spinner = conv.view.spinner
        if kind == "status":
            if message in {"Sending request to provider...", "Processing response..."}:
                live.status = ""
            else:
                live.status = str(message or "")
                spinner.update_label(live.status or "Thinking")
        elif kind in {"tool", "tool_end"}:
            live.tool_events.append(message)
            conv.state.last_tool_events = list(live.tool_events)
            if conv is self._active:
                # Only the tool list changed; a full sidebar refresh reads settings from disk.
                self.query_one("#tool-event-log", ToolEventLog).update_events(conv.state.last_tool_events)
        elif kind == "tool_detail":
            try:
                detail = dict(message)  # type: ignore[arg-type]
            except Exception:
                detail = {"type": "detail", "message": str(message)}
            live.tool_details.append(detail)
            conv.state.last_tool_details = list(live.tool_details)
            live.upsert_tool_item(detail)
            if (
                str(detail.get("type") or "") == "tool_finished"
                and detail.get("success")
                and self._tool_mutates_workspace(str(detail.get("tool_name") or ""))
            ):
                live.mutated_workspace = True
                self._file_index.invalidate()  # the tool may have created or removed files
        elif kind == "warn":
            live.warnings.append(message)
        elif kind == "reasoning":
            live.append_thinking(str(message))
            if not live.status:
                spinner.update_label("Reasoning")
        elif kind == "text":
            live.append_text(str(message))
            if not live.status:
                spinner.update_label("Streaming response")
        elif kind == "steer_applied":
            spinner.update_label("Using your update")
        else:
            live.status = str(message or "")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:

        if event.worker.name == "codebase_scan":
            context = self._codebase_scan_context
            if context is None or event.worker is not context[0]:
                return
            _, owner, original_cwd = context
            if event.state in {WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED}:
                self._codebase_scan_context = None
                self._release_scan_spinner()
            if event.state == WorkerState.SUCCESS:
                result: CodebaseScanResult = event.worker.result
                # A tab switch, close or /cd must not redirect a late result.
                if owner in self._conversations and owner.state.cwd == original_cwd:
                    owner.state.cwd = result.root
                    owner.state.agent = None
                    self._save_session(owner)
                    if owner is self._active:
                        self._refresh_prompt_label()
                        self._refresh_status_bar()
                        self._refresh_sidebar()
                self._append_cmd_log(
                    "Codebase memory scan complete: 100%\n"
                    f"  files={result.files_indexed} changed={result.changed_files} "
                    f"deleted={result.files_deleted} chunks={result.chunks_indexed}\n"
                    f"  saved={result.db_path}"
                )
                self._codebase_scan_progress = None
            elif event.state == WorkerState.ERROR:
                self._append_cmd_log(f"Codebase scan failed: {event.worker.error}", style="bold #f43f5e")
                self._codebase_scan_progress = None
            elif event.state == WorkerState.CANCELLED:
                self._append_cmd_log("Codebase scan cancelled.", style="#fbbf24")
                self._codebase_scan_progress = None
            if self._memory_refresh_queued:
                self._memory_refresh_queued = False
                self._request_background_memory_refresh(reason="queued-post-scan")
            return

        if event.worker.name == "codebase_refresh":
            if event.state == WorkerState.SUCCESS:
                result: CodebaseRefreshResult = event.worker.result
                if result.changed_files or result.files_deleted:
                    self._append_cmd_log(
                        "Background memory refresh complete\n"
                        f"  changed={result.changed_files} deleted={result.files_deleted} "
                        f"chunks={result.chunks_indexed}",
                        style="#38bdf8",
                    )
            elif event.state == WorkerState.ERROR:
                self._append_cmd_log(
                    f"Background memory refresh failed: {event.worker.error}",
                    style="bold #f43f5e",
                )
            self._memory_refresh_running = False
            if self._memory_refresh_queued:
                self._memory_refresh_queued = False
                self._request_background_memory_refresh(reason="queued-post-refresh")
            return

        if event.worker.name != "llm_call":
            return
        conv = self._conversation_for_worker(event.worker)
        if conv is None or conv.live is None:
            # A superseded or closed run finishing late must not touch any chat.
            return
        if event.state == WorkerState.SUCCESS:
            self._finish_turn(conv, event.worker.result)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            detail = f"{type(error).__name__}: {error}" if error is not None else "unknown error"
            self._finish_turn(conv, conv.live.partial_result(TURN_FAILED, detail))
        elif event.state == WorkerState.CANCELLED:
            self._finish_turn(conv, conv.live.partial_result(TURN_CANCELLED, None))

    def _finish_turn(self, conv: Conversation, result: ChatResult) -> None:
        """Save the turn whatever its outcome and turn the live view into it.

        Failed and cancelled turns keep their partial output, and the user can
        keep talking in the same conversation afterwards. Queued prompts for
        this conversation start right after.
        """
        live = conv.live
        assert live is not None
        if conv.run_id is not None:
            self._runs.pop(conv.run_id, None)
        conv.end_run()
        conv.view.spinner.stop()
        if live.blocks and not result.assistant_blocks:
            result.assistant_blocks = list(live.blocks)
        elif live.steered:
            # Keep the "you steered here" notes; the backend's blocks lack them.
            result.assistant_blocks = [*result.assistant_blocks, *(
                {"type": "steer", "text": text} for text in live.steered
            )]
        if live.thinking_text and not result.thinking_text:
            result.thinking_text = live.thinking_text.strip()
        if not result.attachments:
            result.attachments = list(live.display_attachments)

        state = conv.state
        # Recorded with the backend and model the run started with, even if
        # the user switched either one while it was running.
        record_turn(state, live.raw_prompt, result, persist=False, backend=live.backend, model=live.model_name)
        if result.status == TURN_COMPLETED:
            self.session_saver.run_task(summary_for_turn(state, live.raw_prompt, result).record)
        self._request_background_memory_refresh(reason=f"post-run-{result.status}")

        # Auto-generate title from first prompt
        if len(state.transcript) == 1 and not state.session_label:
            state.session_label = generate_session_title_from_prompt(state.transcript[0].prompt)
        self._save_session(conv)

        state.last_tool_events = list(result.tool_events)
        state.last_tool_details = list(result.tool_details)
        state.last_warnings = list(result.warnings)
        if live.flush_timer is not None:
            live.flush_timer.stop()
            live.flush_timer = None
        self._complete_pending_turn(conv, state.transcript[-1])
        self._reset_live_preview(conv)

        conv.last_status = result.status
        conv.unread = conv is not self._active
        if conv.unread:
            title = f"Chat {self._position(conv)}: {conv.title}"
            if result.status == TURN_FAILED:
                self.notify(result.error or "Run failed", title=title, severity="error", timeout=6)
            else:
                self.notify("Reply finished" if result.status == TURN_COMPLETED else "Run stopped", title=title, timeout=4)

        # Steering that arrived after the last model round runs as the next prompt.
        for text in reversed(result.unapplied_steering):
            self._enqueue(conv, text, front=True, note="Your update arrived after the reply finished; sending it next.")

        self._refresh_tabs()
        if conv is self._active:
            self._refresh_status_bar()
            self._refresh_sidebar()
        if conv.queue:
            self.call_after_refresh(self._run_next_queued, conv)
        else:
            self._refresh_queue_bar()

    def action_cmd_dispatch(self, cmd: str, arg: str) -> None:
        """Central action handler called by CommandPalette entries."""
        self._dispatch_command(cmd, arg)

    def action_copy_last(self) -> None:
        """Copy the last assistant response to clipboard."""
        turns = self._state.transcript
        if turns:
            last_resp = turns[-1].response
            try:
                self.copy_to_clipboard(last_resp)
                self.notify("Copied last response to clipboard!", title="Copied", severity="information")
            except Exception as e:
                self.notify(f"Copy failed: {e}", title="Error", severity="error")
        else:
            self.notify("No response to copy.", severity="warning")

    def _dispatch_command(self, cmd: str, arg: str) -> None:
        """Route a command name + argument to the appropriate handler."""
        cmd_log = self.query_one("#cmd-log", RichLog)
        cmd_log.clear()
        self._command_results.clear()
        cmd_log.add_class("visible")
        self.query_one("#boot-screen").display = False
        self.query_one("#main-container").remove_class("home-view")
        self.query_one("#main-container").add_class("command-view")
        s = self._state

        app = self
        class CmdLogProxy:
            def add_system_message(self, content, style="dim #71717a"):
                from rich.text import Text
                if isinstance(content, str):
                    import re
                    cleaned = re.sub(r"\033\[[0-9;]*m", "", content)
                    app._write_cmd_log(Text(f"  {cleaned}", style=style))
                else:
                    app._write_cmd_log(content)
                    
            def add_command_result(self, content):
                from rich.text import Text
                if isinstance(content, str):
                    import re
                    cleaned = re.sub(r"\033\[[0-9;]*m", "", content)
                    app._write_cmd_log(Text(f"  {cleaned}", style="#a78bfa"))
                else:
                    app._write_cmd_log(content)

        chat_log = CmdLogProxy()

        if cmd == "help":
            chat_log.add_system_message(self._build_help_text())
        elif cmd == "exit":
            self.exit()
        elif cmd == "clear":
            self.action_clear_chat()
        elif cmd == "status":
            chat_log.add_system_message(self._build_status_text())
        elif cmd == "sessions":
            input_area = self.query_one("#chat-input", InputArea)
            input_area.text = "/load "
            input_area.cursor_location = (0, 6)
            cmd_log.remove_class("visible")
            self._show_command_argument_suggestions("load", "")
            try: self.query_one("#suggestion-list", OptionList).focus()
            except Exception: pass
        elif cmd == "history":
            try:
                limit = int(arg) if arg else None
                if limit is not None and limit < 1:
                    raise ValueError
            except ValueError:
                chat_log.add_command_result("Usage: /history [positive number of turns]")
                return
            chat_log.add_system_message(self._build_history_text(limit))
        elif cmd == "models":
            chat_log.add_system_message(self._build_models_text())
        elif cmd == "tools":
            chat_log.add_system_message(self._build_tools_text())
        elif cmd == "details":
            normalized = arg.strip().lower()
            if normalized in {"", "toggle"}:
                self._show_tool_details = not self._show_tool_details
            elif normalized in {"on", "true", "1"}:
                self._show_tool_details = True
            elif normalized in {"off", "false", "0"}:
                self._show_tool_details = False
            else:
                chat_log.add_command_result("Usage: /details <toggle|on|off>")
                return
            chat_log.add_command_result(
                f"Tool details {'enabled' if self._show_tool_details else 'disabled'}."
            )
            for conv in self._conversations:
                self._rebuild_chat_log(conv)
            self._refresh_status_bar()
            self._refresh_sidebar()
            return
        elif cmd == "new" or cmd == "reset":
            # A new chat opens next to the current one, which keeps running.
            conv = self._open_conversation(self._fresh_state(label=arg or None))
            self._save_session(conv)
            self._write_cmd_log(
                f"? New chat {self._position(conv)} ({conv.state.session_id})"
                + (f" {arg}" if arg else "")
            )
        elif cmd == "backend":
            if not arg:
                self._open_provider_picker()
            else:
                self._start_backend_switch(arg, chat_log)
        elif cmd == "model":
            if not arg:
                input_area = self.query_one("#chat-input", InputArea)
                input_area.text = "/model "
                input_area.cursor_location = (0, 7)
                cmd_log.remove_class("visible")
                self._show_command_argument_suggestions("model", "")
                try: self.query_one("#suggestion-list", OptionList).focus()
                except Exception: pass
            else:
                self._handle_model_switch(arg)
        elif cmd in {"reasoning", "thinking"}:
            capability = get_thinking_capability(s)
            if capability is None:
                chat_log.add_command_result("Thinking controls are not available for the current backend/model.")
                self._refresh_status_bar()
                self._refresh_sidebar()
                return
            if not arg:
                self.on_thinking_badge_activated(ThinkingBadge.Activated())
                return
            try:
                message = apply_thinking_value(s, arg, persist=False)
                self._save_session()
                chat_log.add_command_result(message)
            except ValueError:
                choices = ", ".join(option.label for option in capability.options)
                chat_log.add_command_result(f"Usage: /{cmd} <{choices}>")
            self._refresh_status_bar()
            self._refresh_sidebar()
            return
        elif cmd == "mode":
            from .tui_harness_policy import normalize_harness_mode, HARNESS_MODES
            if not arg:
                chat_log.add_command_result(
                    f"Mode: {s.harness_mode}  Available: {', '.join(HARNESS_MODES)}"
                )
            else:
                try:
                    s.harness_mode = normalize_harness_mode(arg)
                    s.agent = None
                    self._save_session()
                    chat_log.add_command_result(f"✓ Mode set to {s.harness_mode}")
                except ValueError:
                    chat_log.add_command_result(f"Available: {', '.join(HARNESS_MODES)}")
            self._refresh_status_bar()
        elif cmd == "rounds":
            try:
                rounds = int(arg)
            except ValueError:
                rounds = 0
            if 1 <= rounds <= 1000:
                s.max_rounds = rounds
                self._save_session()
                chat_log.add_command_result(f"✓ max_rounds set to {arg}")
            else:
                chat_log.add_command_result("Usage: /rounds <1-1000>")
        elif cmd == "cd":
            if not arg:
                chat_log.add_command_result("Usage: /cd <dir>")
            else:
                path_arg = arg.strip().strip('"').strip("'")
                path = Path(path_arg).expanduser()
                target = (s.cwd / path).resolve() if not path.is_absolute() else path.resolve()
                if target.exists() and target.is_dir():
                    s.cwd = target
                    s.agent = None
                    self._save_session()
                    chat_log.add_command_result(f"✓ cwd set to {target}")
                    self._refresh_prompt_label()
                    self._refresh_sidebar()
                else:
                    chat_log.add_command_result(f"✗ Not found: {target}")
        elif cmd == "autoresearch":
            if arg.lower() not in {"on", "off"}:
                chat_log.add_command_result(f"Auto research is {'on' if s.autoresearch else 'off'}. Usage: /autoresearch <on|off>")
                return
            s.autoresearch = arg.lower() == "on"
            s.agent = None
            self._save_session()
            chat_log.add_command_result(f"✓ autoresearch={s.autoresearch}")
        elif cmd == "research":
            s.research_instructions = arg or None
            s.agent = None
            self._save_session()
            chat_log.add_command_result("✓ research_instructions updated")
        elif cmd == "codebase":
            self._handle_codebase(arg, chat_log)
        elif cmd == "sandbox":
            self._handle_sandbox(arg)
        elif cmd == "apikey":
            self._handle_apikey(arg)
        elif cmd == "load":
            if not arg:
                input_area = self.query_one("#chat-input", InputArea)
                input_area.text = "/load "
                input_area.cursor_location = (0, 6)
                cmd_log.remove_class("visible")
                self._show_command_argument_suggestions("load", "")
                try: self.query_one("#suggestion-list", OptionList).focus()
                except Exception: pass
            else:
                self._write_cmd_log(self._load_session_into_tab(arg))
        elif cmd in {"close", "tabs", "switch", "chats"}:
            self._handle_conversation_command(cmd, arg)
        elif cmd == "queue":
            self._handle_queue_command(arg)
        elif cmd == "steer":
            self._handle_steer_command(arg)
        elif cmd == "open":
            if not arg:
                chat_log.add_command_result("Usage: /open <session_id>")
            else:
                chat_log.add_system_message(self._build_session_open_text(arg))
        elif cmd == "codex":
            self._handle_codex_auth(arg, chat_log)
        elif cmd == "codex-login":
            self._handle_codex_auth("login", chat_log)
        elif cmd == "compose":
            chat_log.add_system_message("Compose: use Shift+Enter for newlines, Enter to submit.")
        elif cmd == "unknown":
            chat_log.add_command_result("Unknown command. Press Ctrl+P for available commands.")
        else:
            chat_log.add_command_result(f"Unknown: /{cmd}")

    # ── Model / Sandbox / API key handlers ───────────────────────────────

    def _write_cmd_log(self, content: Any, *, style: str = "#a78bfa") -> None:
        """Append a string or Rich renderable to the command output panel."""
        from rich.text import Text

        cmd_log = self.query_one("#cmd-log", RichLog)
        cmd_log.add_class("visible")
        self.query_one("#boot-screen").display = False
        self.query_one("#main-container").remove_class("home-view")
        self.query_one("#main-container").add_class("command-view")
        if isinstance(content, str):
            cleaned = re.sub(r"\033\[[0-9;]*m", "", content)
            self._command_results.append(Text(f"  {cleaned}", style=style))
        else:
            self._command_results.append(content)
        self.call_after_refresh(self._render_command_results)

    def _render_command_results(self) -> None:
        log = self.query_one("#cmd-log", RichLog)
        log.clear()
        for content in self._command_results:
            log.write(content, width=max(1, log.scrollable_content_region.width - 1), scroll_end=False)
        self.query_one("#input-hints").update("  Esc back   Ctrl+O read output   PageUp/Down scroll   Ctrl+P commands")

    def _dismiss_command_output(self) -> None:
        from .tui_shortcuts import hint_bar_text
        self.query_one("#main-container").remove_class("command-view")
        home = not self._state.transcript and self._active.live is None
        self.query_one("#main-container").set_class(home, "home-view")
        self.query_one("#boot-screen").display = home
        self.query_one("#cmd-log").remove_class("visible")
        self.query_one("#input-hints").update(hint_bar_text())
        self._focus_input()

    def on_resize(self, event: events.Resize) -> None:
        width = event.size.width
        if width < 80 and self.query("#sidebar"):
            self.query_one("#sidebar").remove_class("visible")
        if self.query("#status-hints"):
            self.query_one("#status-hints").display = width >= 110
            self.query_one("#status-sandbox").display = width >= 75
        if self._command_results:
            self.call_after_refresh(self._render_command_results)

    def _run_blocking_command(
        self, label: str, work: Callable[[], T], done: Callable[[T], None],
    ) -> None:
        """Run ``work`` on a thread, then ``done(result)`` on the UI thread.

        For slow command I/O (subprocesses, SDK imports, client setup) that
        used to freeze the UI while it ran.
        """
        import asyncio

        async def job() -> None:
            try:
                result = await asyncio.to_thread(work)
            except Exception as exc:
                self._write_cmd_log(f"{label} failed: {exc}", style="bold #f43f5e")
                return
            done(result)

        self.run_worker(job(), name=f"command:{label}", group=_COMMAND_WORKER_GROUP, exclusive=False, exit_on_error=False)

    def _model_choices(self) -> list[str]:
        if self._state.backend == "codex":
            from .tui_codex_metadata import get_codex_models

            return [item.model for item in get_codex_models()]
        from .tui_settings import get_provider_models, load_provider_settings, provider_settings_path

        settings = load_provider_settings(provider_settings_path(self._state.session_store.file_path))
        return get_provider_models(settings, self._state.backend)

    def _refresh_codex_models(self, *, show: bool = False) -> None:
        from .tui_codex_backend import detect_codex_bin
        from .tui_codex_metadata import refresh_codex_models, get_codex_model

        codex_bin = self._state.codex_bin or detect_codex_bin()
        if not codex_bin:
            if show:
                self._write_cmd_log("Codex CLI not found. Install: npm install -g @openai/codex")
            return

        def ready(source: str) -> None:
            self._codex_catalog_source = source
            for conv in self._conversations:
                model = get_codex_model(conv.state.codex_model)
                if conv.state.backend == "codex" and model and conv.state.reasoning_effort not in model.efforts:
                    conv.state.reasoning_effort = model.default_effort
                    self._save_session(conv)
            self._refresh_status_bar()
            self._update_suggestions(self.query_one(InputArea))
            if show:
                self._write_cmd_log(self._build_models_text())

        self._run_blocking_command("Codex models", lambda: refresh_codex_models(codex_bin), ready)

    def _start_backend_switch(self, provider_name: str, chat_log: Any) -> None:
        self._backend_switch_seq += 1
        seq = self._backend_switch_seq
        chat_log.add_command_result(f"Switching to {provider_name.strip().lower()}...")
        state = self._state

        def done(switch: BackendSwitch) -> None:
            if seq != self._backend_switch_seq:
                return  # a newer /backend superseded this one
            if switch.setup_provider:
                self._open_provider_setup(switch.setup_provider, switching=True)
                return
            if apply_backend_switch(state, switch):
                owner = next((c for c in self._conversations if c.state is state), None)
                if owner:
                    self._save_session(owner)
            self._write_cmd_log(switch.message)
            self._refresh_status_bar()
            self._refresh_prompt_label()
            self._refresh_sidebar()

        self._run_blocking_command(
            "backend switch", lambda: prepare_backend_switch(state, provider_name), done,
        )

    def _load_session_into_tab(self, arg: str) -> str:
        """Open a saved session in its own chat, or switch to it if already open."""
        from . import tui as old_tui

        probe = self._fresh_state()
        record = old_tui._load_session_record(probe, arg.strip())
        if record is None:
            # Produces the "not found, recent sessions: ..." message.
            return re.sub(r"\033\[[0-9;]*m", "", old_tui._load_session_hierarchical(probe, arg) or "")
        already_open = next((c for c in self._conversations if c.state.session_id == record.session_id), None)
        if already_open is not None:
            self._activate(already_open)
            return f"Session {record.session_id} is already open in chat {self._position(already_open)}."
        old_tui._load_session_into_state(probe, record)
        conv = self._open_conversation(probe)
        self._save_session(conv)
        return f"Loaded session {probe.session_id} into chat {self._position(conv)} with {len(probe.transcript)} turns."

    def _handle_conversation_command(self, cmd: str, arg: str) -> None:
        arg = arg.strip()
        if cmd in {"tabs", "chats"}:
            lines = []
            for position, conv in enumerate(self._conversations, start=1):
                flags = []
                if conv.running:
                    flags.append("running")
                if conv.queue:
                    flags.append(f"{len(conv.queue)} queued")
                if conv.unread:
                    flags.append("new reply")
                marker = "*" if conv is self._active else " "
                detail = f"  [{', '.join(flags)}]" if flags else ""
                lines.append(f"{marker}{position}  {conv.title}  ({conv.state.backend}){detail}")
            lines.append("F1..F9, Alt+Left/Right, or /switch <n> to switch; Ctrl+N for a new chat; /close [n] to close.")
            self._write_cmd_log("\n".join(lines))
            return
        target = self._active
        if arg:
            try:
                position = int(arg)
            except ValueError:
                position = 0
            if self._conversation_by_position(position) is None:
                self._write_cmd_log(f"No chat {arg}. Open chats: 1-{len(self._conversations)}.")
                return
            target = self._conversation_by_position(position)  # type: ignore[assignment]
        if cmd == "switch":
            if not arg:
                self._write_cmd_log("Usage: /switch <n>")
                return
            self._activate(target)
            return
        self._write_cmd_log(self._close_conversation(target))

    def _handle_queue_command(self, arg: str) -> None:
        conv = self._active
        if arg.strip().lower() == "clear":
            dropped = len(conv.queue)
            conv.queue.clear()
            self._refresh_queue_bar()
            self._refresh_tabs()
            self._write_cmd_log(f"Cleared {dropped} queued message{'s' if dropped != 1 else ''}.")
            return
        if not conv.queue:
            self._write_cmd_log("Nothing queued. Messages sent while a reply is running wait here.")
            return
        lines = [f"{index}. {item.text[:120]}" for index, item in enumerate(conv.queue, start=1)]
        lines.append("/queue clear drops them" + ("; Ctrl+G steers the newest into the run." if self._can_steer(conv) else "."))
        self._write_cmd_log("\n".join(lines))

    def _handle_steer_command(self, arg: str) -> None:
        text = arg.strip()
        conv = self._active
        if not text:
            self._write_cmd_log("Usage: /steer <message>  (adds it to the running reply)")
            return
        if not conv.running:
            self._start_run(conv, text)
            return
        if self._steer(conv, text):
            self._write_cmd_log("Sent to the running reply; the model sees it at its next step.")
            return
        self._enqueue(conv, text, note="This backend cannot take input mid-run; queued to run next.")

    def _handle_codex_auth(self, arg: str, chat_log: Any) -> None:
        import os
        import shlex
        from contextlib import nullcontext

        from . import tui_codex_backend as codex_backend

        try:
            parts = shlex.split(arg or "", posix=(os.name != "nt"))
        except ValueError:
            chat_log.add_command_result("Close the quotation marks in /codex arguments and try again.")
            return
        action = parts[0].lower() if parts else ""
        extra_args = parts[1:]
        if action not in {"login", "logout", "status", "account", "models", "doctor", "repair-config"}:
            chat_log.add_command_result("Usage: /codex <login|logout|status|account|models|doctor|repair-config> [codex CLI flags]")
            return

        codex_bin = self._state.codex_bin or codex_backend.detect_codex_bin()
        if not codex_bin:
            chat_log.add_command_result("Codex CLI not found. Install: npm install -g @openai/codex")
            return
        self._state.codex_bin = codex_bin
        if action == "models":
            self._refresh_codex_models(show=True)
            return

        def _format_result(result: Any, *, fallback: str) -> str:
            output = str(getattr(result, "output", "") or "").strip()
            if output:
                issue = codex_backend.detect_codex_config_issue(output)
                if issue in {
                    codex_backend.CodexConfigIssue.INVALID_SERVICE_TIER_DEFAULT,
                    codex_backend.CodexConfigIssue.UNSUPPORTED_SERVICE_TIER,
                }:
                    return (
                        f"{output}\n\n"
                        "Known fix: run /codex repair-config, then retry /codex status or your prompt."
                    )
                return output
            return fallback

        try:
            if action == "status" and extra_args:
                chat_log.add_command_result("Checking Codex login status...")

                def show_status(result: Any) -> None:
                    self._write_cmd_log(_format_result(
                        result,
                        fallback="Codex is logged in." if result.return_code == 0 else f"Codex status exited: {result.return_code}",
                    ))

                self._run_blocking_command(
                    "codex status",
                    lambda: codex_backend.run_codex_login_status(codex_bin, extra_args),
                    show_status,
                )
                return

            if action == "doctor":
                suspend_context = self.suspend() if hasattr(self, "suspend") else nullcontext()
                chat_log.add_command_result("Starting codex doctor...")
                with suspend_context:
                    result = codex_backend.run_codex_doctor(codex_bin, extra_args)
                chat_log.add_command_result(
                    "Codex doctor completed." if result.return_code == 0 else f"Codex doctor exited: {result.return_code}"
                )
                return

            if action in {"account", "status"}:
                chat_log.add_command_result("Loading Codex account...")
                usage_lines = list(self._state.last_usage_lines)
                last_warnings = list(self._state.last_warnings)

                def load_account() -> tuple[Any, Any]:
                    status = codex_backend.run_codex_login_status(codex_bin)
                    info = codex_backend.build_codex_account_info(
                        codex_bin=codex_bin,
                        last_usage_lines=usage_lines,
                        last_warnings=last_warnings,
                        live=True,
                    )
                    return status, info

                def show_account(loaded: tuple[Any, Any]) -> None:
                    status, info = loaded
                    self._write_cmd_log(self._build_codex_account_view(info, status.output))

                self._run_blocking_command("codex account", load_account, show_account)
                return

            if action == "repair-config":
                message = codex_backend.repair_codex_config_issue(
                    codex_backend.CodexConfigIssue.INVALID_SERVICE_TIER_DEFAULT
                )
                chat_log.add_command_result(message)
                return

            preflight = codex_backend.run_codex_login_status(codex_bin)
            issue = codex_backend.detect_codex_config_issue(preflight.output)
            if issue is not None:
                chat_log.add_command_result(_format_result(preflight, fallback=f"Codex config check exited: {preflight.return_code}"))
                return

            suspend_context = self.suspend() if hasattr(self, "suspend") else nullcontext()
            chat_log.add_command_result(f"Starting codex {action}...")
            with suspend_context:
                if action == "login":
                    result = codex_backend.run_codex_login(codex_bin, extra_args)
                else:
                    result = codex_backend.run_codex_logout(codex_bin, extra_args)

            self._state.codex_session_id = None
            self._save_session()
            if result.return_code == 0:
                chat_log.add_command_result(f"Codex {action} completed.")
                self._refresh_codex_models()
            else:
                chat_log.add_command_result(
                    _format_result(result, fallback=f"Codex {action} exited: {result.return_code}")
                )
        except Exception as exc:
            chat_log.add_command_result(f"Codex {action} failed: {exc}")

    def _build_codex_account_view(self, info: Any, status_output: str = "") -> Any:
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        account = Table(show_header=False, box=None, padding=(0, 2))
        account.add_column("Key", style="bold #38bdf8", no_wrap=True)
        account.add_column("Value", style="#f4f4f6")
        if status_output:
            account.add_row("status", status_output.strip())
        account.add_row("auth", getattr(info, "auth_mode", "unknown"))
        account.add_row("subscription", getattr(info, "plan_type", None) or "unknown")
        account.add_row("subscription status", getattr(info, "subscription_status", "unknown"))
        if getattr(info, "cli_version", None):
            account.add_row("CLI version", info.cli_version)
        account.add_row("email", getattr(info, "email", "unknown"))
        if getattr(info, "name", None):
            account.add_row("name", info.name)
        if getattr(info, "account_id", None):
            account.add_row("account", info.account_id)
        if getattr(info, "last_refresh", None):
            account.add_row("refreshed", info.last_refresh)
        if getattr(info, "access_token_expires", None):
            account.add_row("access expires", info.access_token_expires)
        if getattr(info, "id_token_expires", None):
            account.add_row("id expires", info.id_token_expires)

        config = Table(show_header=True, header_style="bold #c084fc", box=None, padding=(0, 2))
        config.add_column("Setting", style="#38bdf8")
        config.add_column("Value", style="#f4f4f6")
        config_values = getattr(info, "config_values", {}) or {}
        if config_values:
            for key, value in config_values.items():
                config.add_row(str(key), str(value))
        else:
            config.add_row("(none)", "No readable Codex config values")
        config.add_row("config file", str(getattr(info, "config_file", "")))
        config.add_row("auth file", str(getattr(info, "auth_file", "")))
        if getattr(info, "codex_bin", None):
            config.add_row("cli", str(info.codex_bin))

        usage = Table(show_header=False, box=None, padding=(0, 2))
        usage.add_column("Metric", style="#38bdf8", no_wrap=True)
        usage.add_column("Value", style="#f4f4f6")
        usage_lines = list(getattr(info, "usage_lines", []) or [])
        if usage_lines:
            for line in usage_lines:
                key, sep, value = str(line).partition("=")
                usage.add_row(key if sep else "usage", value if sep else str(line))
        else:
            usage.add_row("usage", "No run usage captured in this MTP session yet")
        for warning in list(getattr(info, "limit_warnings", []) or []):
            usage.add_row("limit warning", str(warning))

        note = Text(str(getattr(info, "quota_note", "")), style="dim #71717a")
        return Panel(
            Group(
                Panel(account, title="[bold #34d399]Account[/]", border_style="#334155"),
                Panel(config, title="[bold #a78bfa]Configuration[/]", border_style="#334155"),
                Panel(Group(usage, note), title="[bold #fbbf24]Usage & Limits[/]", border_style="#334155"),
            ),
            title="[bold #38bdf8]Codex[/]",
            border_style="#3f3f46",
        )

    def _handle_codebase(self, arg: str, chat_log: Any) -> None:
        from mtp.codebase import CodebaseMemory

        pieces = arg.split()
        if not pieces:
            self._open_codebase_memory_picker(self._state.cwd, chat_log)
            return

        sub = pieces[0].lower()
        if sub == "status":
            status = CodebaseMemory(self._state.cwd).status()
            chat_log.add_command_result(
                f"Codebase memory {'ON' if status.enabled else 'OFF'}\n"
                f"root={status.root}\n"
                f"files={status.file_count} chunks={status.chunk_count} summaries={status.summary_count}\n"
                f"last_scan_at={status.last_scan_at or '(never)'}"
            )
            return

        if sub != "memory":
            chat_log.add_command_result("Usage: /codebase memory <on|off|show> [root] or /codebase status")
            return

        action = pieces[1].lower() if len(pieces) >= 2 else ""
        root = Path(" ".join(pieces[2:])).expanduser().resolve() if len(pieces) >= 3 else self._state.cwd
        memory = CodebaseMemory(root)

        if action == "show":
            chat_log.add_command_result(self._format_codebase_memory_show(root))
            return

        if action == "off":
            memory.set_enabled(False)
            chat_log.add_command_result(f"Codebase memory OFF for {root}")
            self._refresh_status_bar()
            return

        if action != "on":
            self._open_codebase_memory_picker(root, chat_log)
            return

        self._start_codebase_scan(root, chat_log)

    def _start_codebase_scan(self, root: Path, chat_log: Any) -> None:
        self._show_scan_spinner("Indexing codebase 0%")
        self._codebase_scan_root = root
        self._codebase_scan_progress = "Indexing codebase 0%"
        chat_log.add_command_result(f"Starting codebase memory scan for {root}")
        self._launch_codebase_scan(root)

    def _launch_codebase_scan(self, root: Path) -> None:
        owner = self._active
        worker = self.run_worker(
            self._run_codebase_scan_worker(root),
            name="codebase_scan",
            group=_MEMORY_WORKER_GROUP, exit_on_error=False,
            exclusive=True,
        )
        self._codebase_scan_context = (worker, owner, owner.state.cwd)

    def _show_scan_spinner(self, label: str) -> None:
        """Codebase jobs use their own spinner, separate from any chat's run."""
        self.query_one("#task-spinner", SpinnerWidget).start(label)

    def _release_scan_spinner(self) -> None:
        self.query_one("#task-spinner", SpinnerWidget).stop()

    def _open_codebase_memory_picker(self, root: Path, chat_log: Any) -> None:
        from mtp.codebase import CodebaseMemory

        status = CodebaseMemory(root).status()
        chat_log.add_command_result(
            f"Current project root: {root}\n"
            f"Codebase memory is {'ON' if status.enabled else 'OFF'}.\n"
            "Choose on/off/show with arrows + Enter."
        )
        input_area = self.query_one("#chat-input", InputArea)
        input_area.text = "/codebase memory "
        input_area.cursor_location = (0, len(input_area.text))
        self._populate_and_show_suggestions(["on", "off", "show"], prefix=_ARG_SUGGESTION_PREFIX)
        option_list = self.query_one("#suggestion-list", OptionList)
        option_list.focus()
        option_list.highlighted = 0

    def _handle_model_switch(self, arg: str) -> None:
        chat_log = self
        s = self._state
        if arg.split(None, 1)[0].lower() == "add":
            from .tui_provider_factory import normalize_tui_provider
            from .tui_settings import add_custom_model, load_provider_settings, provider_settings_path, save_provider_settings
            parts = arg.split()
            if len(parts) != 3:
                self._write_cmd_log("Usage: /model add <provider> <model-name>")
                return
            try:
                provider = normalize_tui_provider(parts[1])
                path = provider_settings_path(s.session_store.file_path)
                settings = load_provider_settings(path)
                added = add_custom_model(settings, provider, parts[2])
                save_provider_settings(path, settings)
            except (ValueError, OSError) as exc:
                self._write_cmd_log(f"Could not add model: {exc}")
                return
            self._write_cmd_log(f"{provider}: {parts[2]} {'added' if added else 'already listed'}. Select it with /model {parts[2]} after choosing {provider}.")
            return
        if any(char.isspace() for char in arg):
            self._write_cmd_log("Use a model name without spaces, or /model add <provider> <model-name>.")
            return
        resolved = resolve_model(arg) if s.backend == "codex" else arg.strip()

        if s.backend == "codex":
            s.codex_model = None if resolved.lower() in {"default", "auto"} else resolved
            from .tui_codex_metadata import get_codex_model

            model = get_codex_model(s.codex_model)
            if model and s.reasoning_effort not in model.efforts:
                s.reasoning_effort = model.default_effort
            self._save_session()
            chat_log.add_command_result(f"✓ Codex model: {s.codex_model or '(default)'}")
        else:
            from .tui_settings import (
                provider_settings_path, load_provider_settings,
                ensure_provider_entry, save_provider_settings,
            )
            settings_path = provider_settings_path(s.session_store.file_path)
            settings = load_provider_settings(settings_path)
            entry = ensure_provider_entry(settings, s.backend)
            entry["model"] = resolved
            save_provider_settings(settings_path, settings)
            s.agent = None
            self._save_session()
            chat_log.add_command_result(f"✓ {s.backend} model: {resolved}")
        self._refresh_status_bar()
        self._refresh_sidebar()

    def _handle_sandbox(self, arg: str) -> None:
        chat_log = self
        s = self._state
        if not arg:
            self.action_open_sandbox()
            return
        else:
            mode_map = {
                "readonly": "read-only", "read-only": "read-only",
                "write": "workspace-write", "workspace-write": "workspace-write",
                "full": "danger-full-access", "danger-full-access": "danger-full-access",
            }
            selected = mode_map.get(arg.lower())
            if selected is None:
                self._write_cmd_log("Usage: /sandbox <read-only|workspace-write|danger-full-access>")
                return
            s.codex_sandbox_mode = selected
        self._save_session()
        icons = {"read-only": "🔒", "workspace-write": "✓", "danger-full-access": "⚠"}
        icon = icons.get(s.codex_sandbox_mode, "?")
        chat_log.add_command_result(f"✓ Sandbox: {s.codex_sandbox_mode} {icon}")
        self._refresh_status_bar()

    def _handle_apikey(self, arg: str) -> None:
        from .tui_provider_factory import normalize_tui_provider
        parts = arg.split(None, 2)
        if not parts or parts[0].lower() in {"set", "edit"} and len(parts) < 3:
            provider = parts[1] if len(parts) == 2 else self._state.backend
            if provider == "codex":
                self._open_provider_picker(managing_keys=True)
                return
            try:
                self._open_provider_setup(normalize_tui_provider(provider))
            except ValueError as exc:
                self._write_cmd_log(str(exc))
            return
        if len(parts) == 1 and parts[0].lower() not in {"show", "delete", "set", "list"}:
            try:
                self._open_provider_setup(normalize_tui_provider(parts[0]))
            except ValueError as exc:
                self._write_cmd_log(str(exc))
            return
        if parts[0].lower() in {"delete", "edit"} and len(parts) == 2:
            try:
                self._open_provider_setup(normalize_tui_provider(parts[1]))
            except ValueError as exc:
                self._write_cmd_log(str(exc))
            return
        try:
            from . import tui as old_tui
            result = old_tui._handle_apikey_command(self._state, "" if arg.lower() == "list" else arg)
            if result:
                cleaned = re.sub(r"\033\[[0-9;]*m", "", result)
                self._write_cmd_log(cleaned)
            for conv in self._conversations:
                if conv.state.backend == (parts[1].lower() if len(parts) > 1 else ""):
                    conv.state.agent = None
            self._refresh_status_bar()
        except (OSError, ValueError) as exc:
            self._write_cmd_log(f"Could not update provider settings: {exc}")

    # ── Text builders ────────────────────────────────────────────────────

    def _list_saved_sessions(self) -> list[Any]:
        import json
        from mtp import SessionRecord

        sessions: list[SessionRecord] = []
        if not self._state.session_store.file_path.exists():
            return sessions
        rows = json.loads(self._state.session_store.file_path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            return sessions
        for row in rows:
            if isinstance(row, dict):
                sessions.append(SessionRecord.from_dict(row))
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions

    def _find_session_by_partial_id(self, session_id_input: str) -> Any | None:
        needle = session_id_input.strip().lower()
        if not needle:
            return None
        for record in self._list_saved_sessions():
            if record.session_id.lower() == needle:
                return record
            short_id = record.session_id.split("-")[-1][:8].lower()
            if short_id == needle or record.session_id.lower().endswith(needle):
                return record
        return None

    def _format_tool_detail_line(self, detail: dict[str, Any]) -> str:
        dtype = str(detail.get("type") or "detail")
        if dtype == "plan_received":
            source = detail.get("tool_call_source") or "unknown"
            raw_calls = detail.get("raw_tool_call_count")
            batches = detail.get("derived_batch_count")
            modes = ",".join(str(mode) for mode in detail.get("derived_batch_modes") or []) or "-"
            return f"plan source={source} raw_calls={raw_calls} batches={batches} modes={modes}"
        if dtype == "batch_started":
            batch_index = detail.get("batch_index")
            mode = detail.get("mode") or "unknown"
            call_ids = ",".join(str(item) for item in detail.get("call_ids") or []) or "-"
            return f"batch#{batch_index} mode={mode} call_ids={call_ids}"
        if dtype == "tool_started":
            tool_name = detail.get("tool_name") or "unknown"
            call_id = detail.get("call_id") or "-"
            depends_on = ",".join(str(item) for item in detail.get("depends_on") or []) or "-"
            return f"start {tool_name} call_id={call_id} depends_on={depends_on}"
        if dtype == "tool_finished":
            tool_name = detail.get("tool_name") or "unknown"
            call_id = detail.get("call_id") or "-"
            success = detail.get("success")
            cached = detail.get("cached")
            return f"finish {tool_name} call_id={call_id} success={success} cached={cached}"
        return str(detail)

    def _build_session_open_text(self, session_id_input: str) -> Any:
        from rich.console import Group
        from rich.panel import Panel
        from rich.text import Text

        record = self._find_session_by_partial_id(session_id_input)
        if record is None:
            return f"Session not found: {session_id_input}"

        tui_meta = record.metadata.get("tui", {}) if isinstance(record.metadata, dict) else {}
        tui_meta = tui_meta if isinstance(tui_meta, dict) else {}
        transcript = tui_meta.get("transcript") or []

        header = Text()
        header.append(f"session={record.session_id}\n", style="bold #c084fc")
        header.append(f"updated_at={record.updated_at}\n", style="#71717a")
        if tui_meta.get("session_label"):
            header.append(f"label={tui_meta['session_label']}\n", style="#f4f4f6")
        header.append(f"backend={tui_meta.get('backend') or 'unknown'}", style="#34d399")

        renderables: list[Any] = [header]
        if not transcript:
            renderables.append(Text("\n\nNo transcript turns stored.", style="#71717a"))
        else:
            start_index = max(1, len(transcript) - 9)
            for index, item in enumerate(transcript[-10:], start=start_index):
                if not isinstance(item, dict):
                    continue
                prompt = str(item.get("prompt") or "").replace("\n", " ")[:120]
                response = str(item.get("response") or "").replace("\n", " ")[:160]
                block = Text()
                block.append(f"\n\n#{index}\n", style="bold #fbbf24")
                block.append(f"prompt: {prompt}\n", style="#ec4899")
                block.append(f"response: {response}", style="#8b5cf6")
                detail_items = item.get("tool_details") or []
                if self._show_tool_details and isinstance(detail_items, list):
                    for detail in detail_items[:4]:
                        if isinstance(detail, dict):
                            block.append(f"\n  detail: {self._format_tool_detail_line(detail)}", style="#93c5fd")
                renderables.append(block)

        return Panel(Group(*renderables), title="[bold #38bdf8]Session Viewer[/]", border_style="#3f3f46")

    def _build_help_text(self) -> Any:
        from rich.table import Table
        from rich.panel import Panel
        
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Category", style="bold #c084fc", justify="right")
        table.add_column("Command", style="bold #38bdf8")
        table.add_column("Description", style="#71717a")
        
        table.add_row("Navigation", "/help", "Show this reference")
        table.add_row("", "/exit", "Quit TUI")
        table.add_row("", "/clear", "Clear chat log")
        table.add_row("", "/status", "Show session state")
        table.add_row("", "/sessions", "List saved sessions")
        table.add_row("", "/history", "Show recent turns")
        table.add_row("", "/tools", "Show last tool events")
        table.add_row("", "/details", "Toggle expanded tool metadata")
        table.add_row("", "/open <session_id>", "Open a saved session transcript")
        
        table.add_row("Backend & Model", "/backend <p>", "Switch provider")
        table.add_row("", "/model <name>", "Switch model")
        table.add_row("", "/models", "Show all models")
        table.add_row("", "/apikey", "Manage API keys")
        table.add_row("", "/codex <login|logout|status>", "Manage Codex ChatGPT auth")
        table.add_row("", "/codex account", "Show Codex email, profile, usage")
        table.add_row("", "/codex doctor", "Run Codex diagnostics")
        table.add_row("", "/codex repair-config", "Repair known Codex config issues")
        table.add_row("", "/thinking [level]", "Choose thinking / reasoning effort")
        table.add_row("", "/mode", "Set harness mode")
        table.add_row("", "/sandbox", "Cycle sandbox mode")
        table.add_row("", "/codebase memory", "Enable, disable, or inspect project memory")
        
        table.add_row("Chats", "/new [label]", "Open a new chat; others keep running")
        table.add_row("", "/tabs", "List open chats")
        table.add_row("", "/switch <n>", "Switch to chat n")
        table.add_row("", "/close [n]", "Close a chat (not while it runs)")
        table.add_row("", "/queue [clear]", "Show or drop queued messages")
        table.add_row("", "/steer <text>", "Add a message to the running reply (MTP backends)")

        for index, shortcut in enumerate(SHORTCUTS):
            table.add_row("Keys" if index == 0 else "", shortcut.keys, shortcut.label)
        
        return Panel(table, title="[bold #ec4899]Command Reference[/]", border_style="#3f3f46")

    def _build_status_text(self) -> Any:
        from rich.table import Table
        from rich.panel import Panel
        s = self._state
        thinking = get_thinking_capability(s)
        
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="bold #38bdf8")
        table.add_column("Value", style="#f4f4f6")
        
        table.add_row("session", s.session_id)
        table.add_row("label", s.session_label or "(none)")
        table.add_row("backend", s.backend)
        table.add_row("model", active_model_name(s))
        if s.backend != "codex":
            from .tui_settings import load_provider_settings, provider_settings_path, provider_setup_status
            table.add_row("provider_setup", provider_setup_status(load_provider_settings(provider_settings_path(s.session_store.file_path)), s.backend))
        table.add_row("mode", s.harness_mode)
        if thinking:
            table.add_row(thinking.label, thinking.current_label)
        table.add_row("sandbox", s.codex_sandbox_mode)
        table.add_row("rounds", str(s.max_rounds))
        table.add_row("tool_details", "on" if self._show_tool_details else "off")
        table.add_row("cwd", str(s.cwd))
        table.add_row("turns", str(len(s.transcript)))
        table.add_row("autoresearch", str(s.autoresearch))
        try:
            from mtp.codebase import CodebaseMemory

            memory_status = CodebaseMemory(s.cwd).status()
            table.add_row("codebase_memory", "on" if memory_status.enabled else "off")
            table.add_row("memory_chunks", str(memory_status.chunk_count))
        except Exception:
            table.add_row("codebase_memory", "unknown")
        
        return Panel(table, title="[bold #34d399]Session Status[/]", border_style="#3f3f46")

    def _build_sessions_text(self) -> Any:
        from rich.table import Table
        from rich.panel import Panel

        sessions = []
        try:
            sessions = self._list_saved_sessions()
        except Exception:
            return "No saved sessions."
            
        if not sessions:
            return "No saved sessions."
            
        table = Table(show_header=True, header_style="bold #c084fc", box=None, padding=(0, 2))
        table.add_column("ID", style="#38bdf8")
        table.add_column("Label", style="#f4f4f6")
        table.add_column("Turns", justify="right", style="#71717a")
        table.add_column("Status", style="#34d399")
        
        for rec in sessions[:15]:
            tui = rec.metadata.get("tui", {}) if isinstance(rec.metadata, dict) else {}
            tui = tui if isinstance(tui, dict) else {}
            label = tui.get("session_label", "(unnamed)")
            turns = str(tui.get("turn_count", 0))
            sid = rec.session_id.split("-")[-1][:8]
            active = "● ACTIVE" if rec.session_id == self._state.session_id else ""
            table.add_row(sid, label, turns, active)
            
        return Panel(table, title="[bold #fbbf24]Saved Sessions[/]", border_style="#3f3f46")

    def _build_history_text(self, limit: int | None = None) -> Any:
        from rich.panel import Panel
        from rich.text import Text
        
        turns = self._state.transcript[-limit:] if limit else self._state.transcript
        if not turns:
            return "No turns yet."
            
        group = []
        for i, t in enumerate(turns, 1):
            p = t.prompt.replace("\n", " ")[:80]
            r = t.response.replace("\n", " ")[:80]
            text = Text()
            text.append(f"#{i} {t.created_at} · {t.backend}\n", style="bold #c084fc")
            text.append(f"  ❯ {p}\n", style="#ec4899")
            text.append(f"  ◂ {r}\n", style="#8b5cf6")
            group.append(text)
            
        from rich.console import Group
        return Panel(Group(*group), title="[bold #f472b6]Chat History[/]", border_style="#3f3f46")

    def _build_models_text(self) -> Any:
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Active", style="bold #34d399")
        table.add_column("Num", style="#71717a")
        table.add_column("Model", style="bold #fbbf24")
        table.add_column("Description", style="#f4f4f6")
        
        from .tui_codex_metadata import get_codex_models

        if self._state.backend == "codex":
            for i, model in enumerate(get_codex_models(), 1):
                active = "●" if model.model == self._state.codex_model or model.is_default and self._state.codex_model is None else "○"
                table.add_row(active, f"[{i}]", model.model, model.description + "\nReasoning: " + ", ".join(model.efforts))
        else:
            for model in self._model_choices():
                table.add_row("●" if model == active_model_name(self._state) else "○", "", model, "")
            
        text = Text("\nReasoning: ", style="bold #c084fc")
        text.append(f"{self._state.reasoning_effort}\n", style="#38bdf8")
        capability = get_thinking_capability(self._state)
        if capability:
            text.append("Levels: " + ", ".join(option.label for option in capability.options), style="#71717a")
        if self._state.backend == "codex":
            text.append("\nCatalog: " + self._codex_catalog_source, style="#71717a")
        text.append("\n\nUsage: /model <name>  or  /model add <provider> <name>", style="italic #71717a")
        
        from rich.console import Group
        return Panel(Group(table, text), title="[bold #818cf8]Available Models[/]", border_style="#3f3f46")

    def _build_tools_text(self) -> Any:
        from rich.panel import Panel
        from rich.text import Text
        
        if not self._state.last_tool_events and not self._state.last_tool_details:
            return "No tool events from last turn."
            
        group = []
        text = Text()
        for e in self._state.last_tool_events:
            text.append(f"  ├─ {e}\n", style="#2dd4bf")
        group.append(text)
        if self._show_tool_details and self._state.last_tool_details:
            detail_text = Text("\nDetails:\n", style="bold #93c5fd")
            for detail in self._state.last_tool_details[:12]:
                detail_text.append(f"  - {self._format_tool_detail_line(detail)}\n", style="#93c5fd")
            if len(self._state.last_tool_details) > 12:
                detail_text.append(
                    f"  - ... {len(self._state.last_tool_details) - 12} more detail items\n",
                    style="dim #71717a",
                )
            group.append(detail_text)
        
        if self._state.last_warnings:
            w_text = Text(f"\nWarnings ({len(self._state.last_warnings)}):\n", style="bold #fbbf24")
            for w in self._state.last_warnings:
                w_text.append(f"  ⚠ {w}\n", style="#fbbf24")
            group.append(w_text)
            
        from rich.console import Group
        detail_suffix = " + details" if self._show_tool_details and self._state.last_tool_details else ""
        return Panel(
            Group(*group),
            title=f"[bold #a78bfa]Tool Events ({len(self._state.last_tool_events)}){detail_suffix}[/]",
            border_style="#3f3f46",
        )

    def _build_providers_text(self) -> Any:
        from rich.table import Table
        from rich.panel import Panel
        from .tui_provider_factory import SUPPORTED_TUI_PROVIDERS
        
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Active", style="bold #34d399")
        table.add_column("Provider", style="bold #38bdf8")
        
        active = self._state.backend
        table.add_row("●" if active == "codex" else "○", "codex")
        for p in sorted(SUPPORTED_TUI_PROVIDERS):
            table.add_row("●" if active == p else "○", p)
            
        from rich.text import Text
        text = Text("\nUsage: /backend <provider>", style="italic #71717a")
        from rich.console import Group
        return Panel(Group(table, text), title="[bold #ec4899]Available Providers[/]", border_style="#3f3f46")

    # ── Action bindings ──────────────────────────────────────────────────

    def action_toggle_sidebar(self) -> None:
        if self.size.width < 80:
            self.notify("The sidebar needs 80 columns. Use /status for session details in this window.", timeout=5)
            return
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.toggle()
        self._refresh_sidebar()
        if sidebar.is_open:
            # Files may have changed since the tree was last listed.
            self.query_one("#workspace-tree", WorkspaceTree).refresh_tree(self._state.cwd, force=True)

    def action_clear_chat(self) -> None:
        """Clear what is on screen. A running turn keeps streaming into a fresh view."""
        self.active_chat_log.clear()
        self._command_results.clear()
        self.query_one("#cmd-log", RichLog).clear()
        self._dismiss_command_output()

    def action_focus_output(self) -> None:
        if self.query_one("#cmd-log").has_class("visible"):
            self.query_one("#cmd-log").focus()
        else:
            self.active_chat_log.focus()

    def action_open_sandbox(self) -> None:
        area = self.query_one("#chat-input", InputArea)
        area.text = "/sandbox "
        area.cursor_location = (0, 9)
        self._show_command_argument_suggestions("sandbox", "")

    def action_cycle_sandbox(self) -> None:
        self._handle_sandbox("")
