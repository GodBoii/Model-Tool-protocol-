"""TUI Workers — Async background workers for LLM calls.

All network-bound operations (LLM calls, tool executions) run in
Textual Worker threads so the UI thread never blocks.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, TYPE_CHECKING

from textual.worker import Worker, get_current_worker

from .tui_state import (
    TUIState, ChatResult, TranscriptTurn,
    active_model_name, now_label, serialize_transcript,
    generate_session_title_from_prompt, new_session_id,
    resolve_model, resolve_reasoning,
    BACKENDS, REASONING_EFFORTS,
    MODEL_SHORTCUTS, REASONING_SHORTCUTS,
)

if TYPE_CHECKING:
    from .tui_app import MTPApp


# ── Session persistence ──────────────────────────────────────────────────────

def save_tui_session(state: TUIState) -> None:
    """Persist current TUI state to the session store."""
    from mtp import SessionRecord
    existing = state.session_store.get_session(
        session_id=state.session_id, user_id=state.user_id
    )
    metadata = dict(existing.metadata if existing else {})
    metadata["tui"] = {
        "session_label": state.session_label,
        "backend": state.backend,
        "cwd": str(state.cwd),
        "codex_model": state.codex_model,
        "openai_model": state.openai_model,
        "codex_session_id": state.codex_session_id,
        "reasoning_effort": state.reasoning_effort,
        "harness_mode": state.harness_mode,
        "codex_sandbox_mode": state.codex_sandbox_mode,
        "max_rounds": state.max_rounds,
        "autoresearch": state.autoresearch,
        "research_instructions": state.research_instructions,
        "last_usage_lines": list(state.last_usage_lines),
        "turn_count": len(state.transcript),
        "updated_at": now_label(),
        "transcript": serialize_transcript(state.transcript),
    }
    record = SessionRecord(
        session_id=state.session_id,
        user_id=state.user_id or (existing.user_id if existing else None),
        metadata=metadata,
        messages=list(existing.messages) if existing else [],
        runs=list(existing.runs) if existing else [],
        created_at=existing.created_at if existing else now_label(),
        updated_at=existing.updated_at if existing else now_label(),
    )
    state.session_store.upsert_session(record)


def record_turn(state: TUIState, prompt: str, result: ChatResult) -> None:
    """Record a conversation turn and save."""
    state.transcript.append(TranscriptTurn(
        prompt=prompt,
        response=result.text,
        backend=state.backend,
        model=active_model_name(state),
        attachments=list(result.attachments),
        warnings=list(result.warnings),
        usage_lines=list(result.usage_lines),
        created_at=now_label(),
        tool_details=list(result.tool_details),
    ))
    state.last_usage_lines = list(result.usage_lines)
    state.last_tool_details = list(result.tool_details)
    save_tui_session(state)
    _record_codebase_conversation_summary(state, prompt, result)


def _record_codebase_conversation_summary(state: TUIState, prompt: str, result: ChatResult) -> None:
    """Store a lightweight post-turn summary when codebase memory is enabled."""
    try:
        from mtp.codebase import CodebaseMemory

        memory = CodebaseMemory(state.cwd)
        memory.record_conversation_summary(
            session_id=state.session_id,
            prompt=prompt,
            response=result.text,
            backend=state.backend,
            model=active_model_name(state),
        )
    except Exception:
        return


# ── Attachment collection ────────────────────────────────────────────────────

def _token_looks_like_file_ref(token: str) -> bool:
    if token.startswith("@/"):
        return True
    path_token = token[1:]
    return any(ch in path_token for ch in ("/", "\\", ".")) and "@" not in path_token


def collect_prompt_attachments(
    prompt: str, cwd: Path
) -> tuple[str, list[str], list[str]]:
    """Expand @file references in a prompt."""
    from .tui_state import MAX_ATTACHMENTS, MAX_ATTACHMENT_CHARS
    attachments: list[str] = []
    warnings: list[str] = []
    appended: list[str] = []

    for token in re.findall(r"(?<!\S)@[^\s]+", prompt):
        if not _token_looks_like_file_ref(token):
            continue
        if len(attachments) >= MAX_ATTACHMENTS:
            warnings.append(f"Attachment limit ({MAX_ATTACHMENTS}); skipping.")
            break
        raw_path = token[1:]
        path = Path(raw_path)
        resolved = (cwd / path).resolve() if not path.is_absolute() else path.resolve()
        if not resolved.exists():
            warnings.append(f"Not found: {raw_path}")
            continue
        if not resolved.is_file():
            warnings.append(f"Not a file: {raw_path}")
            continue
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            warnings.append(f"Read error {raw_path}: {exc}")
            continue
        if len(text) > MAX_ATTACHMENT_CHARS:
            text = text[:MAX_ATTACHMENT_CHARS]
            warnings.append(f"Truncated: {raw_path}")
        display_path = (
            str(resolved.relative_to(cwd))
            if resolved.is_relative_to(cwd) else str(resolved)
        )
        attachments.append(display_path)
        appended.append(
            f"[Attached file: {display_path}]\n```text\n{text}\n```"
        )
    if not appended:
        return prompt, attachments, warnings
    return f"{prompt}\n\n" + "\n\n".join(appended), attachments, warnings


# ── LLM execution (runs in Worker thread) ────────────────────────────────────

def run_prompt_blocking(
    state: TUIState,
    prompt: str,
    *,
    emit_callback: Any = None,
) -> ChatResult:
    """Execute an LLM prompt synchronously (called from Worker thread).

    This function blocks and should ONLY be called from a Textual Worker.
    """
    if state.backend == "codex":
        return _run_codex(state, prompt)
    else:
        return _run_mtp(state, prompt, emit_callback=emit_callback)


def _run_codex(state: TUIState, prompt: str) -> ChatResult:
    """Run prompt through Codex CLI backend."""
    from . import tui_codex_backend as codex_backend

    codex_bin = state.codex_bin or codex_backend.detect_codex_bin()
    state.codex_bin = codex_bin
    if not codex_bin:
        return ChatResult(
            text="Codex CLI not found. Install: npm install -g @openai/codex",
            tool_events=[], attachments=[], warnings=[], usage_lines=[],
        )

    conversation_history = [(t.prompt, t.response) for t in state.transcript]
    codex_result = codex_backend.run_codex_prompt(
        codex_bin=codex_bin,
        cwd=state.cwd,
        prompt=prompt,
        model=state.codex_model,
        reasoning_effort=state.reasoning_effort,
        previous_session_id=state.codex_session_id,
        sandbox_mode=state.codex_sandbox_mode,
        conversation_history=conversation_history,
    )
    state.codex_session_id = codex_result.session_id
    return ChatResult(
        text=codex_result.text,
        tool_events=codex_result.tool_events,
        attachments=[],
        warnings=codex_result.warnings,
        usage_lines=codex_result.usage_lines,
    )


def _run_mtp(
    state: TUIState, prompt: str, *, emit_callback: Any = None,
) -> ChatResult:
    """Run prompt through MTP SDK provider backend."""
    from . import tui_mtp_backend as mtp_backend
    from .tui_harness_agent import build_harness_agent
    from .tui_provider_factory import ProviderSelection, build_tui_provider
    from .tui_settings import (
        provider_settings_path, load_provider_settings,
        ensure_provider_entry, DEFAULT_PROVIDER_MODELS,
        is_provider_configured,
    )

    # Initialize agent if needed
    if state.agent is None:
        settings_path = provider_settings_path(state.session_store.file_path)
        settings = load_provider_settings(settings_path)

        if not is_provider_configured(settings, state.backend):
            return ChatResult(
                text=f"Provider {state.backend} is not configured. Use /backend {state.backend} to set up.",
                tool_events=[], attachments=[],
                warnings=["Provider not configured"], usage_lines=[],
            )

        entry = ensure_provider_entry(settings, state.backend)
        model = entry.get("model") or DEFAULT_PROVIDER_MODELS.get(state.backend, "default")
        api_key = entry.get("api_key")
        base_url = entry.get("base_url")
        provider_options: dict[str, Any] | None = None
        if state.backend == "xiaomi":
            provider_options = {}
            if entry.get("thinking_mode"):
                provider_options["thinking_mode"] = entry["thinking_mode"]
            if entry.get("final_thinking_mode"):
                provider_options["final_thinking_mode"] = entry["final_thinking_mode"]
            if not provider_options:
                provider_options = None

        try:
            selection = ProviderSelection(
                provider_name=state.backend, model_name=model,
                api_key=api_key, base_url=base_url,
                provider_options=provider_options,
            )
            provider = build_tui_provider(selection)
            state.agent = build_harness_agent(
                provider=provider, cwd=state.cwd, mode=state.harness_mode,
                autoresearch=state.autoresearch,
                research_instructions=state.research_instructions,
                sandbox_mode=state.codex_sandbox_mode,
            )
        except Exception as exc:
            return ChatResult(
                text=f"Failed to initialize provider: {exc}",
                tool_events=[], attachments=[],
                warnings=[str(exc)], usage_lines=[],
            )

    # Get model name for metrics
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    entry = ensure_provider_entry(settings, state.backend)
    model_name = entry.get("model") or DEFAULT_PROVIDER_MODELS.get(state.backend, "unknown")

    try:
        mtp_result = mtp_backend.run_mtp_prompt(
            agent=state.agent,
            prompt=prompt,
            max_rounds=state.max_rounds,
            emit_live=emit_callback,
            provider_name=state.backend,
            model_name=model_name,
        )
        return ChatResult(
            text=mtp_result.text,
            tool_events=mtp_result.tool_events,
            attachments=[],
            warnings=mtp_result.warnings,
            usage_lines=mtp_result.usage_lines,
            tool_details=mtp_result.tool_details,
        )
    except Exception as exc:
        return ChatResult(
            text=f"Error: {exc}",
            tool_events=[], attachments=[],
            warnings=[str(exc)], usage_lines=[],
        )


# ── Command execution (for commands that need I/O) ───────────────────────────

def switch_backend(state: TUIState, provider_name: str) -> str:
    """Switch the active backend provider. May involve interactive setup."""
    from .tui_provider_factory import (
        ProviderSelection, build_tui_provider, SUPPORTED_TUI_PROVIDERS,
    )
    from .tui_settings import (
        provider_settings_path, load_provider_settings,
        ensure_provider_entry, is_provider_configured,
        DEFAULT_PROVIDER_MODELS, save_provider_settings,
    )
    from .tui_harness_agent import build_harness_agent
    from . import tui_codex_backend as codex_backend

    provider_name = provider_name.lower().strip()

    if provider_name == "codex":
        codex_bin = state.codex_bin or codex_backend.detect_codex_bin()
        if not codex_bin:
            return "Codex CLI not found. Install: npm install -g @openai/codex"
        state.codex_bin = codex_bin
        state.backend = "codex"
        state.agent = None
        save_tui_session(state)
        return "✓ Switched to Codex backend."

    if provider_name not in SUPPORTED_TUI_PROVIDERS:
        return f"Unknown provider: {provider_name}"

    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)

    if not is_provider_configured(settings, provider_name):
        return f"Provider {provider_name} not configured. Set API key first with /apikey set {provider_name} <key>"

    entry = ensure_provider_entry(settings, provider_name)
    model = entry.get("model") or DEFAULT_PROVIDER_MODELS.get(provider_name, "default")
    api_key = entry.get("api_key")
    base_url = entry.get("base_url")
    provider_options: dict[str, Any] | None = None
    if provider_name == "xiaomi":
        provider_options = {}
        if entry.get("thinking_mode"):
            provider_options["thinking_mode"] = entry["thinking_mode"]
        if entry.get("final_thinking_mode"):
            provider_options["final_thinking_mode"] = entry["final_thinking_mode"]
        if not provider_options:
            provider_options = None

    try:
        selection = ProviderSelection(
            provider_name=provider_name, model_name=model,
            api_key=api_key, base_url=base_url, provider_options=provider_options,
        )
        provider = build_tui_provider(selection)
        state.agent = build_harness_agent(
            provider=provider, cwd=state.cwd, mode=state.harness_mode,
            autoresearch=state.autoresearch,
            research_instructions=state.research_instructions,
            sandbox_mode=state.codex_sandbox_mode,
        )
        state.backend = provider_name
        save_tui_session(state)
        return f"✓ Switched to {provider_name} with model {model}."
    except Exception as e:
        return f"Failed: {e}"
