"""MTP TUI App — Main Textual Application.

Async-first, modular Textual App replacing the monolithic tui.py loop.
All LLM calls run in Worker threads; UI never blocks.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from textual.app import App, ComposeResult
from textual.widgets import OptionList, RichLog
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.worker import Worker, WorkerState

from .tui_state import (
    TUIState, ChatResult, active_model_name,
    new_session_id, generate_session_title_from_prompt,
    resolve_model,
    MODEL_PRESETS, REASONING_SHORTCUTS,
)
from .tui_thinking import apply_thinking_value, get_thinking_capability
from .tui_widgets.chat_log import ChatLog, ChatMessage
from .tui_widgets.input_area import InputPanel, InputArea, PromptLabel, AttachmentBadge
from .tui_widgets.status_bar import StatusBar, ThinkingBadge
from .tui_widgets.sidebar import Sidebar, SessionInfo, ToolEventLog, RunMetrics, ShortcutHints, WorkspaceTree
from .tui_widgets.spinner_widget import SpinnerWidget
from .tui_widgets.boot_screen import BootScreen, BootInfo
from .tui_widgets.thinking_dialog import ThinkingDialog
from .tui_commands import MTPCommandProvider, parse_slash_command
from .tui_workers import (
    save_tui_session, record_turn, collect_prompt_attachments,
    run_prompt_blocking, switch_backend,
)

_ARG_SUGGESTION_PREFIX = "-> "
_SUGGESTION_SEPARATOR = " | "


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
        Binding("ctrl+w", "cycle_sandbox", "Sandbox", show=False, priority=True),
        Binding("ctrl+y", "copy_last", "Copy Output", show=True, priority=True),
        Binding("escape", "hide_suggestions", "Hide Suggestions", show=False),
    ]

    def __init__(self, state: TUIState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._state = state
        self._pending_raw_prompt: str = ""
        self._pending_display_prompt: str = ""
        self._pending_display_attachments: list[str] = []
        self._history_index: int | None = None
        self._history_draft: str = ""
        self._pending_attachments: list[str] = []  # Track the raw prompt for recording
        self._input_history: list[str] = []
        self._codebase_scan_progress: str | None = None
        self._codebase_scan_root: Path | None = None
        self._llm_worker_running = False
        self._memory_refresh_running = False
        self._live_status: str = ""
        self._live_blocks: list[dict[str, Any]] = []
        self._live_thinking_text: str = ""
        self._live_tool_events: list[str] = []
        self._live_tool_details: list[dict[str, Any]] = []
        self._live_warnings: list[str] = []
        self._last_live_render_at: float = 0.0
        self._show_tool_details = False
        self._memory_refresh_queued = False
        self._memory_refresh_dirty = False
        self._memory_launch_scan_done = False
        self._current_run_id: str | None = None

    @property
    def state(self) -> TUIState:
        return self._state

    # ── Compose ──────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="main-container"):
                yield BootScreen(id="boot-screen")
                yield ChatLog(id="chat-log")
                yield SpinnerWidget(id="spinner")
                yield RichLog(id="cmd-log", markup=True, highlight=True, wrap=True)
                yield InputPanel(id="input-panel")
            yield Sidebar(id="sidebar")
        yield StatusBar(id="status-bar")

    # ── Mount ────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._refresh_status_bar()
        self._refresh_sidebar()
        self._refresh_prompt_label()
        self._rebuild_chat_log()
        self._show_boot_info()
        self.set_timer(0.5, self._focus_input)
        self._request_background_memory_refresh(reason="startup", prefer_full_scan=True)

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

    def _live_message_active(self) -> bool:
        return bool(
            self._live_blocks
            or self._live_tool_events
            or self._live_tool_details
            or self._live_warnings
        )

    def _current_turn_banner(self) -> str:
        return f"> {self._state.backend} - {active_model_name(self._state)} - mode={self._state.harness_mode}"

    def _clear_pending_turn_display(self) -> None:
        self._pending_display_prompt = ""
        self._pending_display_attachments = []

    def _rebuild_chat_log(self) -> None:
        chat_log = self.query_one("#chat-log", ChatLog)
        chat_log.clear()

        for turn in self._state.transcript:
            chat_log.add_user_message(turn.prompt, turn.attachments)
            chat_log.add_assistant_message(
                ChatMessage(
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
                )
            )

        if self._pending_display_prompt:
            chat_log.add_user_message(self._pending_display_prompt, self._pending_display_attachments)
            chat_log.add_system_message(self._current_turn_banner())
            if self._live_message_active():
                chat_log.set_live_assistant_message(
                    ChatMessage(
                        role="assistant",
                        text="",
                        model=active_model_name(self._state),
                        backend=self._state.backend,
                        tool_events=list(self._live_tool_events),
                        tool_details=list(self._live_tool_details),
                        warnings=list(self._live_warnings),
                        thinking=self._live_thinking_text.strip(),
                        show_tool_details=self._show_tool_details,
                        assistant_blocks=list(self._live_blocks),
                        collapse_thinking=False,
                        is_live=True,
                    )
                )

    def _refresh_status_bar(self) -> None:
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
                is_running=self._llm_worker_running,
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
                message = apply_thinking_value(self._state, result)
            except ValueError as exc:
                self.notify(str(exc), title="Thinking", severity="error")
                return
            self._refresh_status_bar()
            self._refresh_sidebar()
            self.query_one("#chat-log", ChatLog).add_command_result(message)

        self.push_screen(ThinkingDialog(capability), callback=_apply)

    # ── Input handling ───────────────────────────────────────────────────

    def on_input_area_submitted(self, event: InputArea.Submitted) -> None:
        raw = event.value.strip()
        self._history_index = None
        self._history_draft = ""
        
        if raw and (not self._input_history or self._input_history[-1] != raw):
            self._input_history.append(raw)
            
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
        
        if self._pending_attachments:
            raw = " ".join([f"@{att}" for att in self._pending_attachments]) + " " + raw
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

    def action_hide_suggestions(self) -> None:
        if self._llm_worker_running and self._current_run_id and self._state.backend != "codex" and self._state.agent is not None:
            cancelled = self._state.agent.cancel_run(self._current_run_id)
            if cancelled:
                self.query_one("#chat-log", ChatLog).add_system_message("  Interrupt requested...", style="#fbbf24")
            return
        try:
            option_list = self.query_one("#suggestion-list", OptionList)
            if option_list.has_class("visible"):
                option_list.remove_class("visible")
                self.query_one("#chat-input", InputArea).focus()
        except Exception:
            pass

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

    def on_input_area_changed(self, event: InputArea.Changed) -> None:
        input_area = event.text_area
        cursor_row, cursor_col = input_area.cursor_location
        lines = input_area.text.split("\n")
        if cursor_row >= len(lines):
            return
            
        current_line = lines[cursor_row][:cursor_col]
        words = current_line.split()
        
        words = current_line.split()
        
        # Check for command arguments
        if current_line.startswith("/"):
            parts = current_line.split()
            if len(parts) > 1 or (len(parts) == 1 and current_line.endswith(" ")):
                cmd = parts[0][1:].lower()
                partial = parts[1].lower() if len(parts) > 1 else ""
                if len(parts) <= 2:
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
        import os
        from pathlib import Path
        cwd = self._state.cwd
        matches = []
        try:
            for root, dirs, files in os.walk(cwd):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"__pycache__", "node_modules", "venv", ".venv", ".git"}]
                rel_root = Path(root).relative_to(cwd)
                if str(rel_root) == ".":
                    rel_root = Path("")
                for f in files:
                    if f.startswith("."): continue
                    rel_path = (rel_root / f).as_posix()
                    if partial.lower() in rel_path.lower():
                        matches.append(rel_path)
                if len(rel_root.parts) >= 2:
                    dirs.clear()
        except Exception:
            pass
            
        matches.sort()
        matches = matches[:20]
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
            "/reasoning": "Set reasoning level",
            "/thinking": "Set thinking mode",
            "/mode": "Set harness mode",
            "/sandbox": "Cycle sandbox mode",
            "/load": "Load a session",
            "/open": "Open a session",
            "/new": "Start new session",
            "/reset": "Reset session",
            "/rounds": "Set max rounds",
            "/cd": "Change directory",
            "/autoresearch": "Toggle auto-research",
            "/research": "Set research instructions",
            "/codebase": "Codebase memory"
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
            from .tui_state import MODEL_PRESETS
            options = [m for m, d in MODEL_PRESETS]
            matches = [m for m in options if partial in m.lower()]
        elif cmd in ("load", "sessions", "open"):
            import json
            from mtp import SessionRecord
            try:
                sessions = []
                if self._state.session_store.file_path.exists():
                    rows = json.loads(self._state.session_store.file_path.read_text(encoding="utf-8"))
                    for row in rows:
                        sessions.append(SessionRecord.from_dict(row))
                sessions.sort(key=lambda x: x.updated_at, reverse=True)
                for s in sessions[:30]:
                    sid = s.session_id.split("-")[-1][:8]
                    tui = s.metadata.get("tui", {}) if isinstance(s.metadata, dict) else {}
                    label = tui.get("session_label", "")
                    turns = tui.get("turn_count", 0)
                    search_str = f"{sid} {label}".lower()
                    if partial in search_str:
                        display = f"{sid}"
                        if label:
                            display += f"{_SUGGESTION_SEPARATOR}{label}"
                        display += f"  ({turns} turns)"
                        matches.append(display)
            except Exception:
                pass
        elif cmd == "mode":
            from .tui_harness_policy import HARNESS_MODES
            matches = [m for m in HARNESS_MODES if partial in m.lower()]
        elif cmd == "reasoning":
            from .tui_state import REASONING_SHORTCUTS
            matches = [m for m in REASONING_SHORTCUTS.values() if partial in m.lower()]
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
                matches = ["on", "off"]
            elif normalized.startswith("memory "):
                tail = normalized.split(" ", 1)[1]
                matches = [item for item in ["on", "off"] if item.startswith(tail)]
            else:
                matches = [m for m in ["memory", "status"] if m.startswith(normalized)]
            
        if not matches:
            try: self.query_one("#suggestion-list", OptionList).remove_class("visible")
            except Exception: pass
            return
            
        self._populate_and_show_suggestions(matches, prefix=_ARG_SUGGESTION_PREFIX)

    @staticmethod
    def _command_supports_arguments(cmd: str) -> bool:
        return cmd in {
            "backend", "model", "load", "sessions", "open", "mode",
            "reasoning", "details", "sandbox", "codebase",
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
            if current_line.endswith(" "):
                new_line = current_line + val + " "
                start_idx = len(current_line)
            else:
                last_len = len(parts[-1])
                start_idx = len(current_line) - last_len
                new_line = current_line[:start_idx] + val + " "
            
            lines[cursor_row] = new_line + lines[cursor_row][cursor_col:]
            input_area.text = "\n".join(lines)
            input_area.cursor_location = (cursor_row, start_idx + len(val) + 1)
            self._show_next_command_suggestions(input_area)
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

    def _send_prompt(self, raw: str) -> None:
        try:
            self.query_one("#boot-screen", BootScreen).display = False
        except Exception:
            pass

        try:
            cmd_log = self.query_one("#cmd-log", RichLog)
            cmd_log.clear()
            cmd_log.remove_class("visible")
        except Exception:
            pass

        spinner = self.query_one("#spinner", SpinnerWidget)

        expanded, attachments, att_warnings = collect_prompt_attachments(
            raw, self._state.cwd
        )

        self._reset_live_preview()
        self._pending_display_prompt = raw
        self._pending_display_attachments = list(attachments)
        self._rebuild_chat_log()
        spinner.start("Thinking")

        self._turn_start_time = time.monotonic()

        # Store raw prompt for turn recording
        self._pending_raw_prompt = raw
        self._llm_worker_running = True
        self._memory_refresh_dirty = False
        self._current_run_id = f"run-{uuid4().hex[:12]}"

        self.run_worker(
            self._run_llm_worker(expanded, attachments, att_warnings),
            name="llm_call", exclusive=True,
        )

    async def _run_llm_worker(
        self, expanded_prompt: str, attachments: list[str], att_warnings: list[str],
    ) -> ChatResult:
        """Worker coroutine — runs blocking LLM call in thread."""
        import asyncio

        def emit_live(kind: str, message: Any) -> None:
            self.call_from_thread(self._handle_live_event, kind, message)

        result = await asyncio.to_thread(
            run_prompt_blocking,
            self._state,
            expanded_prompt,
            emit_callback=emit_live,
            run_id=self._current_run_id,
        )
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
        try:
            self.query_one("#spinner", SpinnerWidget).update_label(self._codebase_scan_progress)
        except Exception:
            pass

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

        if self._llm_worker_running or self._memory_refresh_running or self._codebase_scan_progress is not None:
            self._memory_refresh_queued = True
            return

        should_full_scan = prefer_full_scan or not status.last_scan_at or status.file_count == 0 or status.chunk_count == 0
        if should_full_scan:
            self._memory_launch_scan_done = True
            spinner = self.query_one("#spinner", SpinnerWidget)
            spinner.start("Indexing codebase 0%")
            self._codebase_scan_root = self._state.cwd
            self._codebase_scan_progress = "Indexing codebase 0%"
            self.run_worker(
                self._run_codebase_scan_worker(self._state.cwd),
                name="codebase_scan",
                exclusive=True,
            )
            return

        self._memory_refresh_running = True
        self.run_worker(
            self._run_codebase_refresh_worker(self._state.cwd),
            name="codebase_refresh",
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

    def _append_live_thinking_block(self, text: str) -> None:
        if not text:
            return
        self._live_thinking_text += text
        if self._live_blocks and self._live_blocks[-1].get("type") == "thinking":
            self._live_blocks[-1]["text"] = str(self._live_blocks[-1].get("text") or "") + text
            return
        self._live_blocks.append({"type": "thinking", "text": text})

    def _append_live_text_block(self, text: str) -> None:
        if not text:
            return
        if self._live_blocks and self._live_blocks[-1].get("type") == "text":
            self._live_blocks[-1]["text"] = str(self._live_blocks[-1].get("text") or "") + text
            return
        self._live_blocks.append({"type": "text", "text": text})

    def _ensure_live_tool_group(self, *, batch_index: Any = None, mode: str | None = None) -> dict[str, Any]:
        if self._live_blocks and self._live_blocks[-1].get("type") == "tool_group":
            block = self._live_blocks[-1]
            if batch_index is None or block.get("batch_index") == batch_index:
                if mode and not block.get("mode"):
                    block["mode"] = mode
                return block
        block = {"type": "tool_group", "batch_index": batch_index, "mode": mode or "unknown", "items": []}
        self._live_blocks.append(block)
        return block

    def _upsert_live_tool_item(self, detail: dict[str, Any]) -> None:
        dtype = str(detail.get("type") or "")
        if dtype == "batch_started":
            self._ensure_live_tool_group(batch_index=detail.get("batch_index"), mode=str(detail.get("mode") or "unknown"))
            return

        if dtype not in {"tool_started", "tool_finished"}:
            return
        call_id = str(detail.get("call_id") or detail.get("tool_name") or "tool")
        for block in self._live_blocks:
            if block.get("type") != "tool_group":
                continue
            for item in block.get("items") or []:
                if str(item.get("call_id") or "") == call_id:
                    item["status"] = "running" if dtype == "tool_started" else ("completed" if detail.get("success") else "failed")
                    item["reasoning"] = detail.get("reasoning")
                    item["cached"] = detail.get("cached")
                    item["error"] = detail.get("error")
                    if detail.get("result_preview") is not None:
                        item["result_preview"] = detail.get("result_preview")
                    if detail.get("started_at_ms") is not None:
                        item["started_at_ms"] = detail.get("started_at_ms")
                    if detail.get("finished_at_ms") is not None:
                        item["finished_at_ms"] = detail.get("finished_at_ms")
                    return

        block = self._ensure_live_tool_group(batch_index=detail.get("batch_index"))
        block["items"].append(
            {
                "call_id": call_id,
                "tool_name": str(detail.get("tool_name") or "tool"),
                "status": "running" if dtype == "tool_started" else ("completed" if detail.get("success") else "failed"),
                "reasoning": detail.get("reasoning"),
                "cached": detail.get("cached"),
                "error": detail.get("error"),
                "started_at_ms": detail.get("started_at_ms"),
                "finished_at_ms": detail.get("finished_at_ms"),
                "result_preview": detail.get("result_preview"),
            }
        )

    @staticmethod
    def _tool_mutates_workspace(tool_name: str) -> bool:
        return tool_name.startswith(("edit.", "shell.", "test."))

    def _reset_live_preview(self) -> None:
        self._live_status = ""
        self._live_blocks = []
        self._live_thinking_text = ""
        self._live_tool_events = []
        self._live_tool_details = []
        self._live_warnings = []
        self._last_live_render_at = 0.0
        try:
            self.query_one("#chat-log", ChatLog).clear_live_assistant_message()
        except Exception:
            pass

    def _render_live_preview(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_live_render_at < 0.10:
            return
        self._last_live_render_at = now
        if not self._pending_display_prompt:
            return
        self.query_one("#chat-log", ChatLog).set_live_assistant_message(
            ChatMessage(
                role="assistant",
                text="",
                model=active_model_name(self._state),
                backend=self._state.backend,
                tool_events=list(self._live_tool_events),
                tool_details=list(self._live_tool_details),
                warnings=list(self._live_warnings),
                thinking=self._live_thinking_text.strip(),
                show_tool_details=self._show_tool_details,
                assistant_blocks=list(self._live_blocks),
                collapse_thinking=False,
                is_live=True,
            )
        )

    def _handle_live_event(self, kind: str, message: Any) -> None:
        spinner = self.query_one("#spinner", SpinnerWidget)
        if kind == "status":
            if message in {"Sending request to provider...", "Processing response..."}:
                self._live_status = ""
            else:
                self._live_status = message
                spinner.update_label(message or "Thinking")
        elif kind in {"tool", "tool_end"}:
            self._live_tool_events.append(message)
            self._state.last_tool_events = list(self._live_tool_events)
            self._refresh_sidebar()
        elif kind == "tool_detail":
            try:
                detail = dict(message)  # type: ignore[arg-type]
            except Exception:
                detail = {"type": "detail", "message": str(message)}
            self._live_tool_details.append(detail)
            self._state.last_tool_details = list(self._live_tool_details)
            self._upsert_live_tool_item(detail)
            if (
                str(detail.get("type") or "") == "tool_finished"
                and detail.get("success")
                and self._tool_mutates_workspace(str(detail.get("tool_name") or ""))
            ):
                self._memory_refresh_dirty = True
        elif kind == "warn":
            self._live_warnings.append(message)
        elif kind == "reasoning":
            self._append_live_thinking_block(str(message))
            if not self._live_status:
                spinner.update_label("Reasoning")
        elif kind == "text":
            self._append_live_text_block(str(message))
            if not self._live_status:
                spinner.update_label("Streaming response")
        else:
            self._live_status = message
        self._render_live_preview()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        spinner = self.query_one("#spinner", SpinnerWidget)

        if event.worker.name == "codebase_scan":
            if event.state == WorkerState.SUCCESS:
                spinner.stop()
                result: CodebaseScanResult = event.worker.result
                self._state.cwd = result.root
                self._state.agent = None
                save_tui_session(self._state)
                self._refresh_prompt_label()
                self._refresh_status_bar()
                self._append_cmd_log(
                    "Codebase memory scan complete: 100%\n"
                    f"  files={result.files_indexed} changed={result.changed_files} "
                    f"deleted={result.files_deleted} chunks={result.chunks_indexed}\n"
                    f"  saved={result.db_path}"
                )
                self._codebase_scan_progress = None
            elif event.state == WorkerState.ERROR:
                spinner.stop()
                self._append_cmd_log(f"Codebase scan failed: {event.worker.error}", style="bold #f43f5e")
                self._codebase_scan_progress = None
            elif event.state == WorkerState.CANCELLED:
                spinner.stop()
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

        if event.state in {WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED}:
            self._llm_worker_running = False
            self._current_run_id = None

        if event.state == WorkerState.SUCCESS:
            spinner.stop()
            result: ChatResult = event.worker.result
            if self._live_blocks and not result.assistant_blocks:
                result.assistant_blocks = list(self._live_blocks)
            if self._live_thinking_text and not result.thinking_text:
                result.thinking_text = self._live_thinking_text.strip()

            # Record the turn with the original raw prompt
            record_turn(self._state, self._pending_raw_prompt, result)
            self._request_background_memory_refresh(reason="post-run")

            # Auto-generate title from first prompt
            if len(self._state.transcript) == 1 and not self._state.session_label:
                self._state.session_label = generate_session_title_from_prompt(
                    self._state.transcript[0].prompt
                )
                save_tui_session(self._state)

            self._state.last_tool_events = list(result.tool_events)
            self._state.last_tool_details = list(result.tool_details)
            self._state.last_warnings = list(result.warnings)
            self._clear_pending_turn_display()
            self._reset_live_preview()
            self._rebuild_chat_log()
            self._refresh_status_bar()
            self._refresh_sidebar()

        elif event.state == WorkerState.ERROR:
            spinner.stop()
            self._clear_pending_turn_display()
            self._reset_live_preview()
            self._rebuild_chat_log()
            self.query_one("#chat-log", ChatLog).add_system_message(
                f"Error: {event.worker.error}", style="bold #f43f5e"
            )
            self._request_background_memory_refresh(reason="post-run-error")

        elif event.state == WorkerState.CANCELLED:
            spinner.stop()
            self._clear_pending_turn_display()
            self._reset_live_preview()
            self._rebuild_chat_log()
            self.query_one("#chat-log", ChatLog).add_system_message(
                "Request cancelled.", style="#fbbf24"
            )
            self._request_background_memory_refresh(reason="post-run-cancelled")

        self._refresh_status_bar()

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
        cmd_log.add_class("visible")
        s = self._state

        class CmdLogProxy:
            def add_system_message(self, content, style="dim #71717a"):
                from rich.text import Text
                if isinstance(content, str):
                    import re
                    cleaned = re.sub(r"\033\[[0-9;]*m", "", content)
                    cmd_log.write(Text(f"  {cleaned}", style=style))
                else:
                    cmd_log.write(content)
                    
            def add_command_result(self, content):
                from rich.text import Text
                if isinstance(content, str):
                    import re
                    cleaned = re.sub(r"\033\[[0-9;]*m", "", content)
                    cmd_log.write(Text(f"  {cleaned}", style="#a78bfa"))
                else:
                    cmd_log.write(content)

        chat_log = CmdLogProxy()

        if cmd == "help":
            chat_log.add_system_message(self._build_help_text())
            input_area = self.query_one("#chat-input", InputArea)
            input_area.text = "/"
            input_area.cursor_location = (0, 1)
            self._show_command_suggestions("/")
            try: self.query_one("#suggestion-list", OptionList).focus()
            except Exception: pass
        elif cmd == "exit":
            self.exit()
        elif cmd == "clear":
            self.query_one("#chat-log", ChatLog).clear()
            cmd_log.remove_class("visible")
            try:
                self.query_one("#boot-screen", BootScreen).display = True
            except Exception:
                pass
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
            limit = int(arg) if arg and arg.isdigit() else None
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
            self._rebuild_chat_log()
            self._refresh_status_bar()
            self._refresh_sidebar()
            return
        elif cmd == "new" or cmd == "reset":
            s.session_id = new_session_id()
            s.session_label = arg or None
            s.transcript = []
            s.last_usage_lines = []
            s.agent = None
            s.codex_session_id = None
            save_tui_session(s)
            self._clear_pending_turn_display()
            self._reset_live_preview()
            self._rebuild_chat_log()
            chat_log.add_command_result(
                f"✓ New session {s.session_id}" + (f" ({arg})" if arg else "")
            )
            self._refresh_status_bar()
            self._refresh_prompt_label()
        elif cmd == "backend":
            if not arg:
                input_area = self.query_one("#chat-input", InputArea)
                input_area.text = "/backend "
                input_area.cursor_location = (0, 9)
                cmd_log.remove_class("visible")
                self._show_command_argument_suggestions("backend", "")
                try: self.query_one("#suggestion-list", OptionList).focus()
                except Exception: pass
            else:
                result = switch_backend(s, arg)
                chat_log.add_command_result(result)
                self._refresh_status_bar()
                self._refresh_prompt_label()
                self._refresh_sidebar()
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
                choices = ", ".join(option.label for option in capability.options)
                chat_log.add_command_result(f"Usage: /{cmd} <{choices}>")
                self._refresh_status_bar()
                self._refresh_sidebar()
                return
            try:
                message = apply_thinking_value(s, arg)
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
                    save_tui_session(s)
                    chat_log.add_command_result(f"✓ Mode set to {s.harness_mode}")
                except ValueError:
                    chat_log.add_command_result(f"Available: {', '.join(HARNESS_MODES)}")
            self._refresh_status_bar()
        elif cmd == "rounds":
            if arg and arg.isdigit() and int(arg) >= 1:
                s.max_rounds = int(arg)
                save_tui_session(s)
                chat_log.add_command_result(f"✓ max_rounds set to {arg}")
            else:
                chat_log.add_command_result("Usage: /rounds <positive-int>")
        elif cmd == "cd":
            if not arg:
                chat_log.add_command_result("Usage: /cd <dir>")
            else:
                target = Path(arg).expanduser().resolve()
                if target.exists() and target.is_dir():
                    s.cwd = target
                    s.agent = None
                    save_tui_session(s)
                    chat_log.add_command_result(f"✓ cwd set to {target}")
                    self._refresh_prompt_label()
                else:
                    chat_log.add_command_result(f"✗ Not found: {target}")
        elif cmd == "autoresearch":
            s.autoresearch = arg.lower() == "on"
            s.agent = None
            save_tui_session(s)
            chat_log.add_command_result(f"✓ autoresearch={s.autoresearch}")
        elif cmd == "research":
            s.research_instructions = arg or None
            s.agent = None
            save_tui_session(s)
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
                chat_log.add_system_message(f"Loading session: {arg}...")
                try:
                    from . import tui as old_tui
                    result = old_tui._load_session_hierarchical(s, arg)
                    cleaned = re.sub(r"\033\[[0-9;]*m", "", result or "")
                    chat_log.add_command_result(cleaned)
                except Exception as e:
                    chat_log.add_command_result(f"Load failed: {e}")
                self._refresh_status_bar()
                self._refresh_prompt_label()
        elif cmd == "open":
            if not arg:
                chat_log.add_command_result("Usage: /open <session_id>")
            else:
                chat_log.add_system_message(self._build_session_open_text(arg))
        elif cmd == "codex-login":
            from . import tui_codex_backend as codex_backend
            codex_bin = s.codex_bin or codex_backend.detect_codex_bin()
            if codex_bin:
                rc = codex_backend.run_codex_login(codex_bin)
                chat_log.add_command_result("✓ Login completed" if rc == 0 else f"Login exited: {rc}")
            else:
                chat_log.add_command_result("Codex CLI not found")
        elif cmd == "compose":
            chat_log.add_system_message("Compose: use Shift+Enter for newlines, Enter to submit.")
        elif cmd == "unknown":
            chat_log.add_command_result("Unknown command. Press Ctrl+P for available commands.")
        else:
            chat_log.add_command_result(f"Unknown: /{cmd}")

    # ── Model / Sandbox / API key handlers ───────────────────────────────

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
            self._reset_live_preview()
            return

        if sub != "memory":
            chat_log.add_command_result("Usage: /codebase memory <on|off|show> [root] or /codebase status")
            return

        action = pieces[1].lower() if len(pieces) >= 2 else ""
        root = Path(" ".join(pieces[2:])).expanduser().resolve() if len(pieces) >= 3 else self._state.cwd
        memory = CodebaseMemory(root)

        if action == "show":
            chat_log.add_command_result(self._format_codebase_memory_show(root))
            self._reset_live_preview()
            return

        if action == "off":
            memory.set_enabled(False)
            chat_log.add_command_result(f"Codebase memory OFF for {root}")
            self._refresh_status_bar()
            return

        if action != "on":
            self._open_codebase_memory_picker(root, chat_log)
            return

        spinner = self.query_one("#spinner", SpinnerWidget)
        spinner.start("Indexing codebase 0%")
        self._codebase_scan_root = root
        self._codebase_scan_progress = "Indexing codebase 0%"
        chat_log.add_command_result(f"Starting codebase memory scan for {root}")
        self.run_worker(
            self._run_codebase_scan_worker(root),
            name="codebase_scan",
            exclusive=True,
        )

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
        chat_log = self.query_one("#chat-log", ChatLog)
        s = self._state
        resolved = resolve_model(arg)

        if s.backend == "codex":
            s.codex_model = None if resolved.lower() in {"default", "auto"} else resolved
            save_tui_session(s)
            chat_log.add_command_result(f"✓ Codex model: {s.codex_model or '(default)'}")
        else:
            from .tui_settings import (
                provider_settings_path, load_provider_settings,
                ensure_provider_entry, save_provider_settings,
            )
            self._reset_live_preview()
            settings_path = provider_settings_path(s.session_store.file_path)
            settings = load_provider_settings(settings_path)
            entry = ensure_provider_entry(settings, s.backend)
            entry["model"] = resolved
            save_provider_settings(settings_path, settings)
            s.agent = None
            save_tui_session(s)
            chat_log.add_command_result(f"✓ {s.backend} model: {resolved}")
        self._refresh_status_bar()
        self._refresh_sidebar()

    def _handle_sandbox(self, arg: str) -> None:
        chat_log = self.query_one("#chat-log", ChatLog)
        s = self._state
        if not arg:
            modes = ["read-only", "workspace-write", "danger-full-access"]
            idx = modes.index(s.codex_sandbox_mode) if s.codex_sandbox_mode in modes else 1
            s.codex_sandbox_mode = modes[(idx + 1) % len(modes)]
        else:
            mode_map = {
                "readonly": "read-only", "read-only": "read-only",
                "write": "workspace-write", "workspace-write": "workspace-write",
                "full": "danger-full-access", "danger-full-access": "danger-full-access",
            }
            s.codex_sandbox_mode = mode_map.get(arg.lower(), arg.lower())
        save_tui_session(s)
        icons = {"read-only": "🔒", "workspace-write": "✓", "danger-full-access": "⚠"}
        icon = icons.get(s.codex_sandbox_mode, "?")
        chat_log.add_command_result(f"✓ Sandbox: {s.codex_sandbox_mode} {icon}")
        self._refresh_status_bar()

    def _handle_apikey(self, arg: str) -> None:
        chat_log = self.query_one("#chat-log", ChatLog)
        try:
            from . import tui as old_tui
            result = old_tui._handle_apikey_command(self._state, arg)
            if result:
                cleaned = re.sub(r"\033\[[0-9;]*m", "", result)
                chat_log.add_command_result(cleaned)
        except Exception as e:
            chat_log.add_command_result(f"Error: {e}")

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
        table.add_row("", "/reasoning", "Set reasoning level / thinking mode")
        table.add_row("", "/thinking", "Set thinking mode")
        table.add_row("", "/mode", "Set harness mode")
        table.add_row("", "/sandbox", "Cycle sandbox mode")
        table.add_row("", "/codebase memory", "Enable, disable, or inspect project memory")
        
        table.add_row("Keys", "Ctrl+P", "Command palette")
        table.add_row("", "Ctrl+B", "Toggle sidebar")
        table.add_row("", "Ctrl+L", "Clear screen")
        table.add_row("", "Ctrl+Y", "Copy last response")
        
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
        
        for i, (m, d) in enumerate(MODEL_PRESETS, 1):
            active = "●" if m == self._state.codex_model else "○"
            table.add_row(active, f"[{i}]", m, d)
            
        text = Text("\nReasoning: ", style="bold #c084fc")
        text.append(f"{self._state.reasoning_effort}\n", style="#38bdf8")
        text.append("Levels: " + " ".join(f"[{k}]={v}" for k, v in REASONING_SHORTCUTS.items()), style="#71717a")
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
        self.query_one("#sidebar", Sidebar).toggle()
        self._refresh_sidebar()

    def action_clear_chat(self) -> None:
        self._clear_pending_turn_display()
        self._reset_live_preview()
        self.query_one("#chat-log", ChatLog).clear()

    def action_cycle_sandbox(self) -> None:
        self._handle_sandbox("")
