"""TUI Workers — Async background workers for LLM calls.

All network-bound operations (LLM calls, tool executions) run in
Textual Worker threads so the UI thread never blocks.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from textual.worker import Worker, get_current_worker

from .tui_state import (
    TURN_CANCELLED, TURN_COMPLETED, TURN_FAILED,
    TUIState, ChatResult, TranscriptTurn,
    active_model_name, now_label, serialize_transcript,
    generate_session_title_from_prompt, new_session_id,
    resolve_model, resolve_reasoning,
    BACKENDS, REASONING_EFFORTS,
    MODEL_SHORTCUTS, REASONING_SHORTCUTS,
)

if TYPE_CHECKING:
    from mtp.simple_agent import MTPAgent

    from .tui_app import MTPApp
    from .tui_codex_backend import CodexRunHandle


# ── Session persistence ──────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """Everything needed to persist a TUI session, detached from live state."""

    store: Any
    session_id: str
    user_id: str | None
    tui_metadata: dict[str, Any]


def snapshot_tui_session(state: TUIState) -> SessionSnapshot:
    """Capture state for saving. No I/O; call on the thread that owns ``state``."""
    return SessionSnapshot(
        store=state.session_store,
        session_id=state.session_id,
        user_id=state.user_id,
        tui_metadata={
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
        },
    )


def write_session_snapshot(snapshot: SessionSnapshot) -> None:
    """Merge ``snapshot`` into the stored session record. Safe off the UI thread."""
    from mtp import SessionRecord
    store = snapshot.store
    existing = store.get_session(session_id=snapshot.session_id, user_id=snapshot.user_id)
    metadata = dict(existing.metadata if existing else {})
    metadata["tui"] = snapshot.tui_metadata
    record = SessionRecord(
        session_id=snapshot.session_id,
        user_id=snapshot.user_id or (existing.user_id if existing else None),
        metadata=metadata,
        messages=list(existing.messages) if existing else [],
        runs=list(existing.runs) if existing else [],
        created_at=existing.created_at if existing else now_label(),
        updated_at=existing.updated_at if existing else now_label(),
    )
    store.upsert_session(record)


def save_tui_session(state: TUIState) -> None:
    """Persist current TUI state to the session store synchronously."""
    write_session_snapshot(snapshot_tui_session(state))


def record_turn(
    state: TUIState,
    prompt: str,
    result: ChatResult,
    *,
    persist: bool = True,
    backend: str | None = None,
    model: str | None = None,
) -> None:
    """Record a conversation turn.

    With ``persist=False`` the caller is responsible for saving the session
    and for the codebase summary (the TUI does both off the UI thread).
    ``backend`` and ``model`` default to the state's current ones; pass the
    values the run started with if they may have changed since.
    """
    state.transcript.append(TranscriptTurn(
        prompt=prompt,
        response=result.text,
        backend=backend or state.backend,
        model=model or active_model_name(state),
        attachments=list(result.attachments),
        warnings=list(result.warnings),
        usage_lines=list(result.usage_lines),
        created_at=now_label(),
        tool_details=list(result.tool_details),
        assistant_blocks=list(result.assistant_blocks),
        thinking_text=result.thinking_text,
        status=result.status,
        error=result.error,
    ))
    state.last_usage_lines = list(result.usage_lines)
    state.last_tool_details = list(result.tool_details)
    if persist:
        save_tui_session(state)
        summary_for_turn(state, prompt, result).record()


@dataclass(frozen=True, slots=True)
class TurnSummary:
    """Inputs for the codebase-memory conversation summary of one turn."""

    cwd: Path
    session_id: str
    prompt: str
    response: str
    backend: str
    model: str

    def record(self) -> None:
        """Store the summary when codebase memory is enabled. Safe off the UI thread."""
        try:
            from mtp.codebase import CodebaseMemory

            CodebaseMemory(self.cwd).record_conversation_summary(
                session_id=self.session_id,
                prompt=self.prompt,
                response=self.response,
                backend=self.backend,
                model=self.model,
            )
        except Exception:
            # Best effort: memory is optional and must never fail a turn.
            return


def summary_for_turn(state: TUIState, prompt: str, result: ChatResult) -> TurnSummary:
    return TurnSummary(
        cwd=state.cwd,
        session_id=state.session_id,
        prompt=prompt,
        response=result.text,
        backend=state.backend,
        model=state.transcript[-1].model if state.transcript else active_model_name(state),
    )


# ── Attachment collection ────────────────────────────────────────────────────

def _token_looks_like_file_ref(token: str) -> bool:
    if token.startswith("@/"):
        return True
    path_token = token[1:]
    return any(ch in path_token for ch in ("/", "\\", ".")) and "@" not in path_token


def _read_attachment_text(path: Path, limit: int) -> tuple[str, bool]:
    """Read at most ``limit`` characters. Returns (text, truncated)."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        text = handle.read(limit + 1)
    if len(text) > limit:
        return text[:limit], True
    return text, False


def collect_prompt_attachments(
    prompt: str, cwd: Path
) -> tuple[str, list[str], list[str]]:
    """Expand @file references in a prompt.

    Reads are bounded to ``MAX_ATTACHMENT_CHARS`` per file, so attaching a
    huge file costs the same as attaching a small one.
    """
    from .tui_state import MAX_ATTACHMENTS, MAX_ATTACHMENT_CHARS
    attachments: list[str] = []
    warnings: list[str] = []
    appended: list[str] = []

    for token in re.findall(r'''(?<!\S)@(?:"[^"]+"|'[^']+'|[^\s]+)''', prompt):
        if not _token_looks_like_file_ref(token):
            continue
        if len(attachments) >= MAX_ATTACHMENTS:
            warnings.append(f"Attachment limit ({MAX_ATTACHMENTS}); skipping.")
            break
        raw_path = token[1:]
        if len(raw_path) >= 2 and raw_path[0] in {'"', "'"} and raw_path[-1] == raw_path[0]:
            raw_path = raw_path[1:-1]
        path = Path(raw_path)
        resolved = (cwd / path).resolve() if not path.is_absolute() else path.resolve()
        if not resolved.exists():
            warnings.append(f"Not found: {raw_path}")
            continue
        if not resolved.is_file():
            warnings.append(f"Not a file: {raw_path}")
            continue
        try:
            text, truncated = _read_attachment_text(resolved, MAX_ATTACHMENT_CHARS)
        except Exception as exc:
            warnings.append(f"Read error {raw_path}: {exc}")
            continue
        if truncated:
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
    run_id: str | None = None,
    codex_handle: CodexRunHandle | None = None,
) -> ChatResult:
    """Execute an LLM prompt synchronously (called from Worker thread).

    This function blocks and should ONLY be called from a Textual Worker.
    ``codex_handle`` lets the UI cancel a codex run; MTP runs are cancelled
    through ``agent.cancel_run(run_id)`` instead.
    """
    if state.backend == "codex":
        return _run_codex(state, prompt, emit_callback=emit_callback, handle=codex_handle)
    else:
        return _run_mtp(state, prompt, emit_callback=emit_callback, run_id=run_id)


def _run_codex(
    state: TUIState,
    prompt: str,
    *,
    emit_callback: Any = None,
    handle: CodexRunHandle | None = None,
) -> ChatResult:
    """Run prompt through Codex CLI backend."""
    from . import tui_codex_backend as codex_backend

    codex_bin = state.codex_bin or codex_backend.detect_codex_bin()
    state.codex_bin = codex_bin
    if not codex_bin:
        message = "Codex CLI not found. Install: npm install -g @openai/codex"
        return ChatResult(
            text="", tool_events=[], attachments=[], warnings=[], usage_lines=[],
            status=TURN_FAILED, error=message,
        )

    conversation_history = [(t.prompt, t.history_reply()) for t in state.transcript]
    from .tui_codex_metadata import get_codex_model

    model_info = get_codex_model(state.codex_model)
    if model_info and state.reasoning_effort not in model_info.efforts:
        return ChatResult(
            text="", tool_events=[], attachments=[], warnings=[], usage_lines=[],
            status=TURN_FAILED,
            error=f"Reasoning {state.reasoning_effort!r} is unavailable for {model_info.model}. Use /reasoning with: {', '.join(model_info.efforts)}.",
        )
    codex_result = codex_backend.run_codex_prompt(
        codex_bin=codex_bin,
        cwd=state.cwd,
        prompt=prompt,
        model=state.codex_model,
        reasoning_effort=state.reasoning_effort,
        previous_session_id=state.codex_session_id,
        sandbox_mode=state.codex_sandbox_mode,
        conversation_history=conversation_history,
        emit_live=emit_callback,
        handle=handle,
    )
    state.codex_session_id = codex_result.session_id
    status, error, text = TURN_COMPLETED, None, codex_result.text
    if handle is not None and handle.cancelled:
        status, text = TURN_CANCELLED, ""
    elif codex_result.return_code not in (0, None):
        status, error, text = TURN_FAILED, codex_result.text, ""
    return ChatResult(
        text=text,
        tool_events=codex_result.tool_events,
        attachments=[],
        warnings=codex_result.warnings,
        usage_lines=codex_result.usage_lines,
        thinking_text="",
        status=status,
        error=error,
    )


def seed_history(
    agent: MTPAgent,
    transcript: list[TranscriptTurn],
    *,
    provider_name: str,
    model_name: str,
    prompt: str = "",
) -> None:
    """Restore recent plain-text turns into an unused agent, once.

    Keep whole user/reply pairs and reserve half the estimated context for
    tools and output. Old tool calls and reasoning are provider-specific and
    deliberately stay out of the new agent's messages.
    """
    from .tui_model_context import get_context_window

    if not transcript:
        return
    history = agent._agent
    if any(message.get("role") != "system" for message in history.messages):
        return
    history._seed_system_messages_if_needed()
    context_window, _ = get_context_window(provider_name, model_name)

    def estimated_tokens(text: str) -> int:
        return (len(text.encode("utf-8")) + 3) // 4 + 8

    budget = max(
        context_window // 2
        - sum(estimated_tokens(str(message.get("content") or "")) for message in history.messages)
        - estimated_tokens(prompt),
        0,
    )
    # Leave space for the current prompt and reply in the agent's own limit.
    message_limit = history.max_history_messages
    pair_limit = 40
    if message_limit > 0:
        pair_limit = min(pair_limit, max((message_limit - len(history.messages) - 2) // 2, 0))
    pairs: list[tuple[str, str]] = []
    for turn in reversed(transcript):
        if len(pairs) >= pair_limit:
            break
        reply = turn.history_reply()
        cost = estimated_tokens(turn.prompt) + estimated_tokens(reply)
        if cost > budget:
            break
        budget -= cost
        pairs.append((turn.prompt, reply))
    for user_text, reply in reversed(pairs):
        history.messages.extend([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": reply},
        ])


def _run_mtp(
    state: TUIState, prompt: str, *, emit_callback: Any = None, run_id: str | None = None,
) -> ChatResult:
    """Run prompt through MTP SDK provider backend."""
    from . import tui_mtp_backend as mtp_backend
    from .tui_harness_agent import build_harness_agent
    from .tui_provider_factory import ProviderSelection, build_tui_provider
    from .tui_settings import (
        provider_settings_path, load_provider_settings,
        ensure_provider_entry, DEFAULT_PROVIDER_MODELS,
        is_provider_configured, provider_api_key,
    )

    # Initialize agent if needed
    if state.agent is None:
        settings_path = provider_settings_path(state.session_store.file_path)
        settings = load_provider_settings(settings_path)

        if not is_provider_configured(settings, state.backend):
            return ChatResult(
                text="", tool_events=[], attachments=[],
                warnings=["Provider not configured"], usage_lines=[],
                status=TURN_FAILED,
                error=f"Provider {state.backend} is not configured. Use /apikey {state.backend} to open setup.",
            )

        entry = ensure_provider_entry(settings, state.backend)
        model = entry.get("model") or DEFAULT_PROVIDER_MODELS.get(state.backend, "default")
        api_key = provider_api_key(settings, state.backend)
        base_url = entry.get("base_url")
        provider_options: dict[str, Any] | None = None
        if state.backend in {"huggingface", "deepinfra", "dashscope", "openai_responses"}:
            provider_options = {key:entry[key] for key in (
                "temperature", "parallel_tool_calls", "max_tokens", "timeout_seconds", "stream_include_usage",
                "region", "workspace_id", "enable_thinking", "max_output_tokens", "reasoning_effort",
            ) if entry.get(key) is not None}
        if state.backend == "groq":
            provider_options = {"reasoning_effort": entry.get("reasoning_effort")}
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
                text="", tool_events=[], attachments=[],
                warnings=[str(exc)], usage_lines=[],
                status=TURN_FAILED, error=f"Failed to initialize provider: {exc}",
            )

    # Get model name for metrics
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    entry = ensure_provider_entry(settings, state.backend)
    model_name = entry.get("model") or DEFAULT_PROVIDER_MODELS.get(state.backend, "unknown")

    try:
        seed_history(
            state.agent, state.transcript,
            provider_name=state.backend, model_name=model_name, prompt=prompt,
        )
        mtp_result = mtp_backend.run_mtp_prompt(
            agent=state.agent,
            prompt=prompt,
            max_rounds=state.max_rounds,
            emit_live=emit_callback,
            provider_name=state.backend,
            model_name=model_name,
            run_id=run_id,
        )
        return ChatResult(
            text=mtp_result.text,
            tool_events=mtp_result.tool_events,
            attachments=[],
            warnings=mtp_result.warnings,
            usage_lines=mtp_result.usage_lines,
            tool_details=mtp_result.tool_details,
            assistant_blocks=mtp_result.assistant_blocks,
            thinking_text=mtp_result.thinking_text,
            status=mtp_result.status,
            error=mtp_result.error,
            unapplied_steering=list(mtp_result.unapplied_steering),
        )
    except Exception as exc:
        return ChatResult(
            text="", tool_events=[], attachments=[],
            warnings=[str(exc)], usage_lines=[],
            status=TURN_FAILED, error=f"{type(exc).__name__}: {exc}",
        )


# ── Command execution (for commands that need I/O) ───────────────────────────

@dataclass(slots=True)
class BackendSwitch:
    """Outcome of preparing a backend switch; ``apply_backend_switch`` commits it."""

    message: str
    backend: str | None = None  # None: the switch failed, leave state alone
    agent: Any = None
    codex_bin: str | None = None
    setup_provider: str | None = None


def switch_backend(state: TUIState, provider_name: str) -> str:
    """Switch the active backend provider synchronously and save the session."""
    switch = prepare_backend_switch(state, provider_name)
    if apply_backend_switch(state, switch):
        save_tui_session(state)
    return switch.message


def apply_backend_switch(state: TUIState, switch: BackendSwitch) -> bool:
    """Commit a prepared switch to ``state``. Returns True if state changed."""
    if switch.backend is None:
        return False
    if switch.codex_bin:
        state.codex_bin = switch.codex_bin
    state.backend = switch.backend
    state.agent = switch.agent
    return True


def prepare_backend_switch(state: TUIState, provider_name: str) -> BackendSwitch:
    """Select a configured backend; create its provider and agent only on a run."""
    from .tui_provider_factory import normalize_tui_provider
    from .tui_settings import (
        provider_settings_path, load_provider_settings,
        ensure_provider_entry, is_provider_configured,
        DEFAULT_PROVIDER_MODELS,
    )
    from . import tui_codex_backend as codex_backend

    provider_name = provider_name.lower().strip()

    if provider_name == "codex":
        codex_bin = state.codex_bin or codex_backend.detect_codex_bin()
        if not codex_bin:
            return BackendSwitch("Codex CLI not found. Install: npm install -g @openai/codex")
        return BackendSwitch("✓ Switched to Codex backend.", backend="codex", codex_bin=codex_bin)

    try:
        provider_name = normalize_tui_provider(provider_name)
    except ValueError:
        return BackendSwitch(f"Unknown provider: {provider_name}")

    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)

    if not is_provider_configured(settings, provider_name):
        return BackendSwitch(
            f"{provider_name} needs setup. Use /apikey {provider_name} or choose another provider.",
            setup_provider=provider_name,
        )

    entry = ensure_provider_entry(settings, provider_name)
    model = entry.get("model") or DEFAULT_PROVIDER_MODELS.get(provider_name, "default")
    return BackendSwitch(f"Selected {provider_name} with model {model}.", backend=provider_name)
