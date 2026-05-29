"""TUI State — Reactive state management for the Textual TUI.

Extracts TUIState and data classes from the monolithic tui.py into a standalone
module so that both the old and new UI paths can share them.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from mtp import Agent, JsonSessionStore, SessionRecord


# ── Constants ────────────────────────────────────────────────────────────────

BACKENDS = {
    "codex", "openai", "groq", "claude", "gemini", "openrouter",
    "mistral", "cohere", "sambanova", "cerebras", "deepseek",
    "togetherai", "fireworksai", "xiaomi",
}
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")
MAX_ATTACHMENTS = 8
MAX_ATTACHMENT_CHARS = 16_000

MODEL_PRESETS: list[tuple[str, str]] = [
    ("gpt-5.4", "Frontier general coding model"),
    ("gpt-5.4-mini", "Faster/cheaper coding model"),
    ("gpt-5.3-codex", "Codex-optimized coding model"),
    ("gpt-5.2", "Previous frontier model"),
]

MODEL_SHORTCUTS = {"1": "gpt-5.4", "2": "gpt-5.4-mini", "3": "gpt-5.3-codex", "4": "gpt-5.2"}

REASONING_SHORTCUTS = {"0": "none", "1": "low", "2": "medium", "3": "high", "4": "xhigh"}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class ChatResult:
    text: str
    tool_events: list[str]
    attachments: list[str]
    warnings: list[str]
    usage_lines: list[str]
    tool_details: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TranscriptTurn:
    prompt: str
    response: str
    backend: str
    model: str
    attachments: list[str]
    warnings: list[str]
    usage_lines: list[str]
    created_at: str
    tool_details: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TUIState:
    backend: str
    codex_model: str | None
    openai_model: str
    max_rounds: int
    cwd: Path
    autoresearch: bool
    research_instructions: str | None
    reasoning_effort: str
    harness_mode: str
    codex_sandbox_mode: str  # "read-only", "workspace-write", or "danger-full-access"
    last_usage_lines: list[str]
    transcript: list[TranscriptTurn]
    session_store: JsonSessionStore
    session_id: str
    session_label: str | None
    user_id: str | None
    agent: Agent.MTPAgent | None = None
    codex_bin: str | None = None
    codex_session_id: str | None = None
    last_tool_events: list[str] = field(default_factory=list)
    last_tool_details: list[dict[str, Any]] = field(default_factory=list)
    last_warnings: list[str] = field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────────────────

def new_session_id() -> str:
    return f"chat-{uuid4().hex[:10]}"


def now_label() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def active_model_name(state: TUIState) -> str:
    if state.backend == "codex":
        return state.codex_model or "(codex-default)"
    from .tui_settings import (
        provider_settings_path, load_provider_settings, preferred_model_for_provider,
    )
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    return preferred_model_for_provider(settings, state.backend)


def generate_session_title_from_prompt(prompt: str, max_words: int = 4, max_chars: int = 50) -> str:
    cleaned = re.sub(r'@[^\s]+', '', prompt)
    cleaned = ' '.join(cleaned.split())
    words = cleaned.split()[:max_words]
    title = ' '.join(words)
    if len(title) > max_chars:
        title = title[:max_chars].rsplit(' ', 1)[0]
        if title:
            title += '...'
    if len(title.strip()) < 3:
        return "Quick chat"
    return title.strip()


def serialize_transcript(turns: list[TranscriptTurn]) -> list[dict[str, Any]]:
    return [
        {
            "prompt": t.prompt, "response": t.response, "backend": t.backend,
            "model": t.model, "attachments": list(t.attachments),
            "warnings": list(t.warnings), "usage_lines": list(t.usage_lines),
            "created_at": t.created_at,
            "tool_details": list(t.tool_details),
        }
        for t in turns
    ]


def deserialize_transcript(payload: Any) -> list[TranscriptTurn]:
    if not isinstance(payload, list):
        return []
    transcript: list[TranscriptTurn] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        transcript.append(TranscriptTurn(
            prompt=str(item.get("prompt") or ""),
            response=str(item.get("response") or ""),
            backend=str(item.get("backend") or "codex"),
            model=str(item.get("model") or ""),
            attachments=[str(x) for x in item.get("attachments") or []],
            warnings=[str(x) for x in item.get("warnings") or []],
            usage_lines=[str(x) for x in item.get("usage_lines") or []],
            created_at=str(item.get("created_at") or now_label()),
            tool_details=[
                detail for detail in item.get("tool_details") or []
                if isinstance(detail, dict)
            ],
        ))
    return transcript


def resolve_model(arg: str) -> str:
    normalized = arg.strip().lower()
    return MODEL_SHORTCUTS.get(normalized, arg.strip())


def resolve_reasoning(arg: str) -> str | None:
    normalized = arg.strip().lower()
    if normalized in REASONING_SHORTCUTS:
        return REASONING_SHORTCUTS[normalized]
    if normalized in REASONING_EFFORTS:
        return normalized
    if normalized in {"extra-high", "extra_high"}:
        return "xhigh"
    return None
