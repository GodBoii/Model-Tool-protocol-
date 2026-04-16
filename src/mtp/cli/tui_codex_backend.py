from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable


_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-5.4": 400_000,
    "gpt-5.4-mini": 400_000,
    "gpt-5.3-codex": 400_000,
    "gpt-5.2": 400_000,
    "gpt-5.2-codex": 400_000,
    "gpt-5.1-codex-max": 400_000,
    "gpt-5-codex": 400_000,
    "gpt-4o": 128_000,
}


@dataclass(slots=True)
class CodexRunResult:
    text: str
    tool_events: list[str]
    warnings: list[str]
    usage_lines: list[str]
    session_id: str | None
    return_code: int


def detect_codex_bin() -> str | None:
    for candidate in ("codex.cmd", "codex"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def run_codex_login(codex_bin: str) -> int:
    proc = subprocess.run([codex_bin, "login"], text=True)
    return int(proc.returncode)


def _format_int(n: int | None) -> str:
    if n is None:
        return "unknown"
    return f"{n:,}"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:.2f}%"


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.isdigit():
            return int(cleaned)
    return None


def _context_usage_lines(model_name: str | None, request_tokens: int | None) -> list[str]:
    normalized_model = (model_name or "").strip()
    window = _MODEL_CONTEXT_WINDOWS.get(normalized_model)
    if window is None:
        return [f"context_window={_format_int(request_tokens)} tokens / window=unknown ({model_name})"]
    if request_tokens is None:
        return [f"context_window=unknown / window={window:,} ({model_name})"]
    used = min(request_tokens, window)
    remaining = max(window - used, 0)
    used_pct = (float(used) / float(window)) * 100.0
    remaining_pct = (float(remaining) / float(window)) * 100.0
    return [
        f"context_window={used:,}/{window:,} used ({_format_pct(used_pct)})",
        f"context_remaining={remaining:,}/{window:,} ({_format_pct(remaining_pct)})",
    ]


def _merge_usage_metrics(total: dict[str, int], usage: dict[str, Any]) -> None:
    for key in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


def _extract_rate_values(payload: Any, found: dict[str, Any]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).lower()
            if "rate" in key_l and any(part in key_l for part in ("remaining", "limit", "reset")):
                found[str(key)] = value
            elif key_l in {
                "x-ratelimit-limit-requests",
                "x-ratelimit-limit-tokens",
                "x-ratelimit-remaining-requests",
                "x-ratelimit-remaining-tokens",
                "limit_requests",
                "limit_tokens",
                "remaining_requests",
                "remaining_tokens",
                "remaining",
                "retry-after",
            }:
                found[str(key)] = value
            _extract_rate_values(value, found)
    elif isinstance(payload, list):
        for item in payload:
            _extract_rate_values(item, found)


def _rate_percentage_line(label: str, remaining: Any, limit: Any, reset: Any) -> str | None:
    remaining_n = _coerce_int(remaining)
    limit_n = _coerce_int(limit)
    if remaining_n is None and limit_n is None:
        return None
    pieces = [label]
    if remaining_n is not None and limit_n is not None and limit_n > 0:
        pct = (float(remaining_n) / float(limit_n)) * 100.0
        pieces.append(f"{remaining_n:,}/{limit_n:,} remaining ({_format_pct(pct)})")
    elif remaining_n is not None:
        pieces.append(f"{remaining_n:,} remaining")
    else:
        pieces.append(f"limit={limit_n:,}")
    if reset is not None:
        pieces.append(f"reset={reset}")
    return f"rate_limit_{pieces[0]}=" + " | ".join(pieces[1:])


def _format_rate_lines(rate_fields: dict[str, Any] | None) -> list[str]:
    if not rate_fields:
        return ["rate_remaining=unknown"]
    normalized = {str(k).lower(): v for k, v in rate_fields.items()}
    request_line = _rate_percentage_line(
        "requests",
        normalized.get("x-ratelimit-remaining-requests") or normalized.get("remaining_requests"),
        normalized.get("x-ratelimit-limit-requests") or normalized.get("limit_requests"),
        normalized.get("x-ratelimit-reset-requests") or normalized.get("retry-after"),
    )
    token_line = _rate_percentage_line(
        "tokens",
        normalized.get("x-ratelimit-remaining-tokens") or normalized.get("remaining_tokens"),
        normalized.get("x-ratelimit-limit-tokens") or normalized.get("limit_tokens"),
        normalized.get("x-ratelimit-reset-tokens") or normalized.get("retry-after"),
    )
    lines = [line for line in (request_line, token_line) if line is not None]
    if lines:
        return lines
    compact = ", ".join(f"{k}={v}" for k, v in sorted(rate_fields.items())[:4])
    return [f"rate_remaining={compact}"]


def _shorten_text(text: str, limit: int = 160) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _extract_codex_session_id(event: dict[str, Any], event_type: str) -> str | None:
    """
    Enhanced session ID extraction with broader pattern matching.
    Looks for thread/session/conversation IDs in multiple locations and formats.
    """
    # Expanded candidate keys to catch more ID formats
    candidate_keys = (
        "thread_id",
        "session_id",
        "conversation_id",
        "chat_id",
        "context_id",
        "id",
        "threadId",
        "sessionId",
        "conversationId",
    )
    
    # Check top-level keys first for started events
    if event_type in {"thread.started", "session.started", "conversation.started", "turn.started"}:
        for key in candidate_keys:
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    
    # Check nested structures
    nested_keys = ("thread", "session", "conversation", "context", "payload", "item", "data", "metadata")
    for nested_key in nested_keys:
        nested = event.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in candidate_keys:
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    
    # Fallback: check ALL top-level keys for any started event
    if "started" in event_type or "begin" in event_type:
        for key in candidate_keys:
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    
    return None


def _parse_codex_json_events(
    stdout_text: str,
    active_model: str | None,
) -> tuple[str, list[str], list[str], list[str], str | None]:
    tool_events: list[str] = []
    warnings: list[str] = []
    usage_lines: list[str] = []
    final_text = ""
    assistant_chunks: list[str] = []
    total_usage: dict[str, int] = {}
    peak_request_tokens: int | None = None
    rate_fields: dict[str, Any] = {}
    tool_state: dict[str, tuple[str, str | None]] = {}
    tool_order: list[str] = []
    codex_session_id: str | None = None
    for raw_line in stdout_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("ERROR:"):
            warnings.append(line)
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(line)
            continue
        event_type = str(event.get("type", "")).strip().lower()
        detected_session_id = _extract_codex_session_id(event, event_type)
        if detected_session_id:
            codex_session_id = detected_session_id
        usage = event.get("usage")
        if isinstance(usage, dict):
            _merge_usage_metrics(total_usage, usage)
            request_tokens = usage.get("total_tokens")
            if not isinstance(request_tokens, int):
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                    request_tokens = input_tokens + output_tokens
                elif isinstance(input_tokens, int):
                    request_tokens = input_tokens
                elif isinstance(output_tokens, int):
                    request_tokens = output_tokens
            if isinstance(request_tokens, int):
                peak_request_tokens = max(peak_request_tokens or 0, request_tokens)
        _extract_rate_values(event, rate_fields)
        tool_name, tool_reasoning, tool_key, _tool_phase = _extract_codex_tool_signal_details(event, event_type)
        if event_type in {"response.output_text.delta", "response.output_text"}:
            delta = event.get("delta") or event.get("text") or ""
            if isinstance(delta, str) and delta:
                assistant_chunks.append(delta)
            continue
        if event_type in {"assistant_message", "message"}:
            text = event.get("text") or event.get("content") or ""
            if isinstance(text, str) and text.strip():
                final_text = text.strip()
            continue
        if tool_name:
            key = tool_key or f"{tool_name}::{len(tool_order)}"
            existing = tool_state.get(key)
            if existing is None:
                tool_state[key] = (tool_name, tool_reasoning)
                tool_order.append(key)
            else:
                prev_name, prev_reasoning = existing
                chosen_name = prev_name or tool_name
                chosen_reasoning = _prefer_tool_reasoning(prev_reasoning, tool_reasoning)
                tool_state[key] = (chosen_name, chosen_reasoning)
            continue
    for key in tool_order:
        name, reasoning = tool_state[key]
        if reasoning:
            tool_events.append(f"{name}: {reasoning}")
        else:
            tool_events.append(name)
    if assistant_chunks:
        final_text = "".join(assistant_chunks).strip() or final_text
    usage_lines.append(
        "tokens(in/out/total/reasoning)="
        f"{_format_int(total_usage.get('input_tokens'))}/"
        f"{_format_int(total_usage.get('output_tokens'))}/"
        f"{_format_int(total_usage.get('total_tokens'))}/"
        f"{_format_int(total_usage.get('reasoning_tokens'))}"
    )
    usage_lines.extend(_context_usage_lines(active_model, peak_request_tokens))

    remaining_pct: str | None = None
    for chunk in (stdout_text, "\n".join(warnings)):
        match = re.search(r"(\d{1,3})%\s+left", chunk, re.IGNORECASE)
        if match:
            remaining_pct = match.group(1) + "%"
            break
    if remaining_pct is not None:
        usage_lines.append(f"rate_remaining={remaining_pct}")
    else:
        usage_lines.extend(_format_rate_lines(rate_fields))

    return final_text, tool_events, warnings, usage_lines, codex_session_id


def parse_codex_json_events(
    stdout_text: str,
    active_model: str | None,
) -> tuple[str, list[str], list[str], list[str], str | None]:
    return _parse_codex_json_events(stdout_text, active_model)


def _prefer_tool_reasoning(existing: str | None, incoming: str | None) -> str | None:
    existing_text = existing.strip() if isinstance(existing, str) and existing.strip() else None
    incoming_text = incoming.strip() if isinstance(incoming, str) and incoming.strip() else None
    if incoming_text is None:
        return existing_text
    if existing_text is None:
        return incoming_text
    existing_is_shell_fallback = existing_text.lower().startswith("running shell command:")
    incoming_is_shell_fallback = incoming_text.lower().startswith("running shell command:")
    if existing_is_shell_fallback and not incoming_is_shell_fallback:
        return incoming_text
    if not existing_is_shell_fallback and incoming_is_shell_fallback:
        return existing_text
    if len(incoming_text) > len(existing_text):
        return incoming_text
    return existing_text


def _extract_codex_tool_signal(event: dict[str, Any], event_type: str) -> tuple[str | None, str | None]:
    name, reasoning, _tool_key, _tool_phase = _extract_codex_tool_signal_details(event, event_type)
    return name, reasoning


def extract_codex_tool_signal(event: dict[str, Any], event_type: str) -> tuple[str | None, str | None]:
    return _extract_codex_tool_signal(event, event_type)


def _extract_codex_tool_signal_details(
    event: dict[str, Any],
    event_type: str,
) -> tuple[str | None, str | None, str | None, str]:
    def _get_str(value: Any) -> str | None:
        if isinstance(value, str):
            text = value.strip()
            return text or None
        return None

    def _event_phase(event_type_value: str) -> str:
        lowered = (event_type_value or "").lower()
        if any(token in lowered for token in ("started", "start", "begin")):
            return "started"
        if any(token in lowered for token in ("finished", "complete", "end")):
            return "finished"
        return "update"

    def _first_nested_reasoning(container: dict[str, Any]) -> str | None:
        for key in ("arguments", "args", "input", "payload", "metadata", "details"):
            nested = container.get(key)
            if not isinstance(nested, dict):
                continue
            for reason_key in ("reasoning", "summary", "thought", "thinking", "description", "message"):
                value = _get_str(nested.get(reason_key))
                if value:
                    return value
        return None

    def _looks_like_shell_command(value: str | None) -> bool:
        if not value:
            return False
        lowered = value.lower()
        markers = (
            "powershell",
            "cmd.exe",
            "/bin/sh",
            "bash -lc",
            "sh -c",
            "pwsh",
        )
        return any(marker in lowered for marker in markers)

    def _normalize_tool_name(name: str | None, *, item_type: str | None) -> str | None:
        if name and name.startswith("functions."):
            name = name[len("functions.") :]
        normalized_item_type = (item_type or "").lower()
        if normalized_item_type in {
            "exec_command",
            "exec_command_begin",
            "exec_command_start",
            "exec_command_end",
            "shell_call",
            "terminal_command",
        }:
            return "shell.run_command"
        if _looks_like_shell_command(name):
            return "shell.run_command"
        return name

    def _tool_key(
        *,
        event_obj: dict[str, Any],
        item_obj: dict[str, Any] | None,
        normalized_name: str | None,
        reasoning: str | None,
    ) -> str | None:
        for source in (item_obj, event_obj):
            if not isinstance(source, dict):
                continue
            for candidate in ("call_id", "id", "tool_call_id", "invocation_id"):
                value = _get_str(source.get(candidate))
                if value:
                    return value
        for source in (item_obj, event_obj):
            if not isinstance(source, dict):
                continue
            for container_key in ("payload", "arguments", "args", "metadata", "details"):
                container = source.get(container_key)
                if not isinstance(container, dict):
                    continue
                for candidate in ("call_id", "id", "tool_call_id", "invocation_id"):
                    value = _get_str(container.get(candidate))
                    if value:
                        return value
        if normalized_name:
            reason_basis = reasoning or ""
            return f"{normalized_name}|{_shorten_text(reason_basis, 80)}"
        return None

    def _from_item(item: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
        item_type = _get_str(item.get("type")) or _get_str(item.get("kind")) or ""
        if item_type in {"message", "assistant_message", "reasoning", "analysis"}:
            return None, None, None
        name = (
            _get_str(item.get("name"))
            or _get_str(item.get("tool_name"))
            or _get_str(item.get("toolName"))
            or _get_str(item.get("command"))
        )
        if not name and item_type in {"function_call", "tool_call", "exec_command", "shell_call"}:
            name = item_type
        normalized_name = _normalize_tool_name(name, item_type=item_type)
        if not normalized_name:
            return None, None, None
        reasoning = (
            _get_str(item.get("reasoning"))
            or _get_str(item.get("summary"))
            or _get_str(item.get("thought"))
            or _get_str(item.get("thinking"))
            or _get_str(item.get("description"))
            or _get_str(item.get("content"))
            or _get_str(item.get("message"))
            or _first_nested_reasoning(item)
        )
        if not reasoning and _looks_like_shell_command(name):
            command_preview = _shorten_text(name or "", 140)
            reasoning = f"running shell command: {command_preview}"
        return normalized_name, reasoning, _tool_key(
            event_obj=event,
            item_obj=item,
            normalized_name=normalized_name,
            reasoning=reasoning,
        )

    if event_type in {"tool_call", "tool.started", "tool.finished", "function_call", "shell_call", "exec_command"}:
        name = (
            _get_str(event.get("tool_name"))
            or _get_str(event.get("name"))
            or _get_str(event.get("function"))
            or _get_str(event.get("command"))
            or event_type
        )
        name = _normalize_tool_name(name, item_type=_get_str(event.get("type")))
        reasoning = (
            _get_str(event.get("reasoning"))
            or _get_str(event.get("summary"))
            or _get_str(event.get("thought"))
            or _get_str(event.get("thinking"))
            or _get_str(event.get("description"))
            or _get_str(event.get("content"))
            or _get_str(event.get("message"))
            or _first_nested_reasoning(event)
        )
        if not reasoning and _looks_like_shell_command(_get_str(event.get("command"))):
            command_preview = _shorten_text(_get_str(event.get("command")) or "", 140)
            reasoning = f"running shell command: {command_preview}"
        return name, reasoning, _tool_key(
            event_obj=event,
            item_obj=None,
            normalized_name=name,
            reasoning=reasoning,
        ), _event_phase(event_type)

    item = event.get("item")
    if isinstance(item, dict):
        name, reasoning, key = _from_item(item)
        if name:
            return name, reasoning, key, _event_phase(event_type)

    payload = event.get("payload")
    if isinstance(payload, dict):
        name, reasoning, key = _from_item(payload)
        if name:
            return name, reasoning, key, _event_phase(event_type)

    return None, None, None, _event_phase(event_type)


def extract_codex_tool_signal_details(
    event: dict[str, Any],
    event_type: str,
) -> tuple[str | None, str | None, str | None, str]:
    return _extract_codex_tool_signal_details(event, event_type)


def _emit_codex_live_line(raw_line: str, emitted: dict[str, Any], emit: Callable[[str, str], None]) -> None:
    line = raw_line.strip()
    if not line:
        return
    if line.startswith("ERROR:"):
        emit("warn", line)
        return
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return
    event_type = str(event.get("type", "")).strip().lower()
    tool_name, tool_reasoning, tool_key, _tool_phase = _extract_codex_tool_signal_details(event, event_type)
    if event_type in {"response.output_text.delta", "response.output_text"}:
        if not emitted.get("assistant_started"):
            emitted["assistant_started"] = True
            emit("status", "assistant is drafting the response")
        return
    if tool_name:
        key = tool_key or f"{tool_name}::{event_type}"
        tool_stream_state = emitted.setdefault("tool_stream_state", {})
        if not isinstance(tool_stream_state, dict):
            tool_stream_state = {}
            emitted["tool_stream_state"] = tool_stream_state
        prev_reasoning = tool_stream_state.get(key)
        if not isinstance(prev_reasoning, str):
            prev_reasoning = None
        chosen_reasoning = _prefer_tool_reasoning(prev_reasoning, tool_reasoning)
        if prev_reasoning is None or chosen_reasoning != prev_reasoning:
            if chosen_reasoning:
                emit("tool", f"{tool_name}: {_shorten_text(chosen_reasoning, 180)}")
            else:
                emit("tool", tool_name)
            tool_stream_state[key] = chosen_reasoning
        return
    if event_type.endswith("started") or event_type in {"run_started", "round_started", "plan_received"}:
        round_id = event.get("round")
        if round_id is not None:
            emit("step", f"{event_type} (round {round_id})")
        else:
            emit("step", event_type)
        return
    if event_type.endswith("failed") or event_type in {"error"}:
        detail = str(event.get("error") or event.get("message") or event_type)
        emit("warn", _shorten_text(detail, 220))


def emit_codex_live_line(raw_line: str, emitted: dict[str, Any], emit: Callable[[str, str], None]) -> None:
    _emit_codex_live_line(raw_line, emitted, emit)


def _build_prompt_with_history(
    current_prompt: str,
    conversation_history: list[tuple[str, str]],
    max_turns: int = 5,
) -> str:
    """
    Build a prompt that includes conversation history for context.
    
    CRITICAL: Format must look like actual conversation, not instructions/placeholders.
    Codex needs to see this as real prior messages, not meta-commentary.
    
    Args:
        current_prompt: The current user query
        conversation_history: List of (user_prompt, assistant_response) tuples
        max_turns: Maximum number of previous turns to include (default 5)
    
    Returns:
        Formatted prompt with conversation history prepended
    """
    if not conversation_history:
        return current_prompt
    
    # Limit to recent turns to avoid token overflow
    recent_history = conversation_history[-max_turns:] if len(conversation_history) > max_turns else conversation_history
    
    # Build conversation history in a natural format
    # Make it look like actual messages, not meta-instructions
    history_parts = []
    
    for user_msg, assistant_msg in recent_history:
        # Truncate very long messages to keep prompt manageable
        user_truncated = user_msg[:500] + "..." if len(user_msg) > 500 else user_msg
        assistant_truncated = assistant_msg[:800] + "..." if len(assistant_msg) > 800 else assistant_msg
        
        # Format as natural conversation continuation
        history_parts.append(f"User: {user_truncated}")
        history_parts.append(f"Assistant: {assistant_truncated}")
        history_parts.append("")  # blank line between exchanges
    
    # Add current query as the next user message
    history_parts.append(f"User: {current_prompt}")
    
    return "\n".join(history_parts)


def _build_codex_exec_command(
    *,
    codex_bin: str,
    cwd: Path,
    output_path: Path,
    prompt: str,
    model: str | None,
    reasoning_effort: str,
    session_id: str | None,
    sandbox_mode: str = "workspace-write",
) -> list[str]:
    """
    Build codex exec command with proper syntax for resume vs fresh sessions.
    
    CRITICAL: codex exec resume does NOT accept -C flag!
    - Fresh session: codex exec -C <cwd> [options] <prompt>
    - Resume session: codex exec resume <session_id> [options] <prompt>
    
    Args:
        sandbox_mode: Sandbox mode for file operations.
                     Options: "read-only", "workspace-write", "danger-full-access"
                     Default: "workspace-write" (allows file edits in workspace)
    """
    if session_id:
        # Resume command: NO -C flag, working directory is inherited from original session
        cmd = [
            codex_bin,
            "exec",
            "resume",
            session_id,
            "--skip-git-repo-check",
            "--json",
            "--output-last-message",
            str(output_path),
        ]
    else:
        # Fresh session: use --cd (or -C) to set working directory
        cmd = [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--cd",  # Use --cd instead of -C for clarity
            str(cwd),
            "--json",
            "--output-last-message",
            str(output_path),
        ]
    
    # Add sandbox mode flag
    # Valid modes: read-only, workspace-write, danger-full-access
    if sandbox_mode in ("read-only", "workspace-write", "danger-full-access"):
        cmd.extend(["--sandbox", sandbox_mode])
    
    if model:
        cmd.extend(["-m", model])
    if reasoning_effort in _REASONING_EFFORTS and reasoning_effort != "none":
        cmd.extend(["-c", f'reasoning_effort="{reasoning_effort}"'])
    cmd.append(prompt)
    return cmd


def _run_codex_command(
    *,
    cmd: list[str],
    emit: Callable[[str], None] | None,
) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    stdout_lines: list[str] = []
    emitted: dict[str, Any] = {}
    if proc.stdout is not None:
        for line in proc.stdout:
            stdout_lines.append(line)
            if emit is not None:
                _emit_codex_live_line(
                    line,
                    emitted,
                    emit=lambda kind, message: emit(f"{kind}:{message}"),
                )
    return proc.wait(), "".join(stdout_lines)


def run_codex_prompt(
    *,
    codex_bin: str,
    cwd: Path,
    prompt: str,
    model: str | None,
    reasoning_effort: str,
    previous_session_id: str | None,
    sandbox_mode: str = "workspace-write",
    conversation_history: list[tuple[str, str]] | None = None,
    emit_live: Callable[[str, str], None] | None = None,
) -> CodexRunResult:
    """
    Run a Codex prompt with optional conversation history injection.
    
    Args:
        codex_bin: Path to codex executable
        cwd: Working directory
        prompt: Current user prompt
        model: Model name
        reasoning_effort: Reasoning effort level
        previous_session_id: Previous Codex session/thread ID for resume
        sandbox_mode: Sandbox mode ("read-only", "workspace-write", "danger-full-access")
        conversation_history: List of (user_prompt, assistant_response) tuples for manual history injection
        emit_live: Optional callback for live event streaming
    
    Returns:
        CodexRunResult with response text, tool events, warnings, usage, and session ID
    """
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as tmp:
        output_path = Path(tmp.name)

    def _emit_adapter(payload: str) -> None:
        if emit_live is None:
            return
        kind, _, message = payload.partition(":")
        emit_live(kind, message)

    try:
        # CRITICAL INSIGHT: codex exec resume maintains Codex's internal thread state,
        # but our TUI has its own transcript that Codex doesn't know about.
        # We need to ALWAYS inject history when available, regardless of session ID.
        
        # Build prompt with history if we have conversation history
        effective_prompt = prompt
        if conversation_history and len(conversation_history) > 0:
            effective_prompt = _build_prompt_with_history(prompt, conversation_history)
            if emit_live:
                emit_live("status", f"Injecting {len(conversation_history)} previous turns for context")
        
        cmd = _build_codex_exec_command(
            codex_bin=codex_bin,
            cwd=cwd,
            output_path=output_path,
            prompt=effective_prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            session_id=previous_session_id,
            sandbox_mode=sandbox_mode,
        )
        return_code, stdout_text = _run_codex_command(cmd=cmd, emit=_emit_adapter if emit_live else None)
        text = output_path.read_text(encoding="utf-8", errors="replace").strip() if output_path.exists() else ""

        parsed_text, tool_events, parse_warnings, usage_lines, detected_session_id = _parse_codex_json_events(
            stdout_text,
            model,
        )
        effective_session_id = detected_session_id or previous_session_id

        if return_code != 0 and previous_session_id:
            # Session/thread can expire or become invalid. Retry once as a fresh run WITH history injection.
            if emit_live:
                emit_live("warn", "Session resume failed, retrying with conversation history injection")
            
            # Build prompt with history for the retry
            retry_prompt = prompt
            if conversation_history:
                retry_prompt = _build_prompt_with_history(prompt, conversation_history)
                if emit_live:
                    emit_live("status", f"Injecting {len(conversation_history)} previous turns for context")
            
            fresh_cmd = _build_codex_exec_command(
                codex_bin=codex_bin,
                cwd=cwd,
                output_path=output_path,
                prompt=retry_prompt,
                model=model,
                reasoning_effort=reasoning_effort,
                session_id=None,
                sandbox_mode=sandbox_mode,
            )
            fresh_return_code, fresh_stdout_text = _run_codex_command(
                cmd=fresh_cmd,
                emit=_emit_adapter if emit_live else None,
            )
            fresh_text = output_path.read_text(encoding="utf-8", errors="replace").strip() if output_path.exists() else ""
            (
                fresh_parsed_text,
                fresh_tool_events,
                fresh_warnings,
                fresh_usage_lines,
                fresh_session_id,
            ) = _parse_codex_json_events(fresh_stdout_text, model)
            if fresh_return_code == 0:
                parse_warnings = [
                    *fresh_warnings,
                    f"Previous Codex session could not be resumed; started a new session.",
                ]
                final_text = fresh_text or fresh_parsed_text or fresh_stdout_text.strip() or "(Codex returned no final text.)"
                return CodexRunResult(
                    text=final_text,
                    tool_events=fresh_tool_events,
                    warnings=parse_warnings,
                    usage_lines=fresh_usage_lines,
                    session_id=fresh_session_id,
                    return_code=fresh_return_code,
                )

        if return_code != 0:
            details = stdout_text.strip()
            hint = "Try: codex login"
            if details:
                return CodexRunResult(
                    text=f"Codex exec failed (exit {return_code}).\n{details}\n{hint}",
                    tool_events=tool_events,
                    warnings=parse_warnings,
                    usage_lines=usage_lines,
                    session_id=effective_session_id,
                    return_code=return_code,
                )
            return CodexRunResult(
                text=f"Codex exec failed (exit {return_code}).\n{hint}",
                tool_events=tool_events,
                warnings=parse_warnings,
                usage_lines=usage_lines,
                session_id=effective_session_id,
                return_code=return_code,
            )

        final_text = text or parsed_text or stdout_text.strip() or "(Codex returned no final text.)"
        return CodexRunResult(
            text=final_text,
            tool_events=tool_events,
            warnings=parse_warnings,
            usage_lines=usage_lines,
            session_id=effective_session_id,
            return_code=return_code,
        )
    finally:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass
