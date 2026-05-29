"""Canonical Textual TUI launcher and small compatibility helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from mtp import JsonSessionStore, SessionRecord

from .tui_codex_backend import _extract_codex_tool_signal
from .tui_codex_backend import _parse_codex_json_events as _parse_codex_json_events_with_session
from .tui_harness_policy import normalize_harness_mode
from .tui_provider_factory import SUPPORTED_TUI_PROVIDERS
from .tui_settings import (
    DEFAULT_PROVIDER_MODELS,
    delete_provider_api_key,
    ensure_provider_entry,
    load_provider_settings,
    provider_settings_path,
    save_provider_settings,
    set_provider_api_key,
)
from .tui_state import TUIState, deserialize_transcript, new_session_id
from .tui_workers import save_tui_session


def _new_session_id() -> str:
    return new_session_id()


def _parse_codex_json_events(stdout_text: str, active_model: str | None) -> tuple[str, list[str], list[str], list[str]]:
    final_text, tool_events, warnings, usage_lines, _session_id = _parse_codex_json_events_with_session(
        stdout_text,
        active_model,
    )
    return final_text, tool_events, warnings, usage_lines


def _list_saved_sessions(store: JsonSessionStore) -> list[SessionRecord]:
    sessions: list[SessionRecord] = []
    try:
        if not store.file_path.exists():
            return sessions
        rows = json.loads(store.file_path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            return sessions
        for row in rows:
            if isinstance(row, dict):
                sessions.append(SessionRecord.from_dict(row))
    except Exception:
        return []
    sessions.sort(key=lambda item: item.updated_at, reverse=True)
    return sessions


def _find_session_by_partial_id(store: JsonSessionStore, partial_id: str) -> SessionRecord | None:
    needle = partial_id.strip().lower()
    if not needle:
        return None
    for record in _list_saved_sessions(store):
        session_id = record.session_id.lower()
        short_id = record.session_id.split("-")[-1][:8].lower()
        if needle in {session_id, short_id} or session_id.endswith(needle):
            return record
    return None


def _load_session_record(state: TUIState, session_id_input: str) -> SessionRecord | None:
    exact = state.session_store.get_session(session_id=session_id_input, user_id=state.user_id)
    if exact is not None:
        return exact
    return _find_session_by_partial_id(state.session_store, session_id_input)


def _load_session_into_state(state: TUIState, record: SessionRecord) -> None:
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    tui_meta = metadata.get("tui") if isinstance(metadata.get("tui"), dict) else {}

    state.session_id = record.session_id
    state.user_id = record.user_id or state.user_id
    state.session_label = tui_meta.get("session_label") if isinstance(tui_meta.get("session_label"), str) else None
    state.backend = str(tui_meta.get("backend") or state.backend)
    state.cwd = Path(str(tui_meta.get("cwd") or state.cwd)).expanduser().resolve()
    state.codex_model = tui_meta.get("codex_model") if isinstance(tui_meta.get("codex_model"), str) else state.codex_model
    state.openai_model = str(tui_meta.get("openai_model") or state.openai_model)
    state.codex_session_id = (
        tui_meta.get("codex_session_id") if isinstance(tui_meta.get("codex_session_id"), str) else None
    )
    state.reasoning_effort = str(tui_meta.get("reasoning_effort") or state.reasoning_effort)
    state.harness_mode = normalize_harness_mode(str(tui_meta.get("harness_mode") or state.harness_mode))
    state.codex_sandbox_mode = str(tui_meta.get("codex_sandbox_mode") or state.codex_sandbox_mode)
    state.max_rounds = int(tui_meta.get("max_rounds") or state.max_rounds)
    state.autoresearch = bool(tui_meta.get("autoresearch", state.autoresearch))
    research = tui_meta.get("research_instructions")
    state.research_instructions = research if isinstance(research, str) and research.strip() else None
    state.last_usage_lines = [str(item) for item in tui_meta.get("last_usage_lines") or []]
    state.transcript = deserialize_transcript(tui_meta.get("transcript"))
    state.agent = None


def _load_session_hierarchical(state: TUIState, session_id_input: str) -> str:
    record = _load_session_record(state, session_id_input.strip())
    if record is None:
        sessions = _list_saved_sessions(state.session_store)
        if not sessions:
            return f"No sessions found. Session database: {state.session_store.file_path}"
        suggestions = ", ".join(record.session_id.split("-")[-1][:8] for record in sessions[:5])
        return f"Session not found: {session_id_input}. Recent sessions: {suggestions}"
    _load_session_into_state(state, record)
    save_tui_session(state)
    return f"Loaded session {state.session_id} with {len(state.transcript)} turns."


def _handle_apikey_command(state: TUIState, arg: str) -> str:
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    parts = arg.split(None, 2)

    if not parts:
        configured = []
        for provider_name in sorted(SUPPORTED_TUI_PROVIDERS):
            entry = ensure_provider_entry(settings, provider_name)
            key = entry.get("api_key")
            if isinstance(key, str) and key:
                masked = f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "*" * len(key)
            else:
                masked = "(not set)"
            configured.append(f"{provider_name}: {masked}")
        return "\n".join(configured)

    command = parts[0].lower()
    if command == "set":
        if len(parts) < 3:
            return "Usage: /apikey set <provider> <key>"
        provider_name = parts[1].lower()
        api_key = parts[2].strip()
        if provider_name not in SUPPORTED_TUI_PROVIDERS:
            return f"Unknown provider: {provider_name}"
        if not api_key:
            return "API key cannot be empty."
        set_provider_api_key(settings, provider_name, api_key)
        save_provider_settings(settings_path, settings)
        if state.backend == provider_name:
            state.agent = None
        masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "*" * len(api_key)
        return f"API key for {provider_name} set to {masked}"

    if command == "delete":
        if len(parts) < 2:
            return "Usage: /apikey delete <provider>"
        provider_name = parts[1].lower()
        if provider_name not in SUPPORTED_TUI_PROVIDERS:
            return f"Unknown provider: {provider_name}"
        deleted = delete_provider_api_key(settings, provider_name)
        save_provider_settings(settings_path, settings)
        if state.backend == provider_name:
            state.agent = None
        return f"API key for {provider_name} deleted" if deleted else f"No API key was set for {provider_name}"

    if command == "show":
        if len(parts) < 2:
            return "Usage: /apikey show <provider>"
        provider_name = parts[1].lower()
        if provider_name not in SUPPORTED_TUI_PROVIDERS:
            return f"Unknown provider: {provider_name}"
        entry = ensure_provider_entry(settings, provider_name)
        api_key = entry.get("api_key")
        return f"{provider_name}: {api_key}" if api_key else f"No API key set for {provider_name}"

    return "Unknown subcommand. Available: set, delete, show"


def run_tui(args: Any) -> int:
    session_store = JsonSessionStore(db_path=args.session_db)
    state = TUIState(
        backend=args.backend,
        codex_model=args.codex_model,
        openai_model=args.openai_model,
        max_rounds=int(args.max_rounds),
        cwd=Path(args.cwd).expanduser().resolve(),
        autoresearch=bool(args.autoresearch),
        research_instructions=args.research_instructions,
        reasoning_effort=str(getattr(args, "reasoning_effort", "medium")),
        harness_mode=normalize_harness_mode(getattr(args, "mode", "code")),
        codex_sandbox_mode="workspace-write",
        last_usage_lines=[],
        transcript=[],
        session_store=session_store,
        session_id=args.session_id or _new_session_id(),
        session_label=None,
        user_id="tui-user",
        codex_session_id=None,
    )
    if args.session_id:
        existing = _load_session_record(state, args.session_id)
        if existing is not None:
            _load_session_into_state(state, existing)
    _ensure_initial_provider_model(state)
    save_tui_session(state)

    try:
        from .tui_app import MTPApp
    except ImportError as exc:
        print("Textual is required for `mtp tui`. Install with: pip install mtpx[ui]", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    app = MTPApp(state=state)
    app.run()
    return 0


def _ensure_initial_provider_model(state: TUIState) -> None:
    if state.backend == "codex":
        return
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    entry = ensure_provider_entry(settings, state.backend)
    entry.setdefault("model", DEFAULT_PROVIDER_MODELS.get(state.backend, "default"))
    save_provider_settings(settings_path, settings)


__all__ = [
    "_extract_codex_tool_signal",
    "_handle_apikey_command",
    "_load_session_hierarchical",
    "_parse_codex_json_events",
    "run_tui",
]
