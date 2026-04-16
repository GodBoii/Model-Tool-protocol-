from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import sys
import textwrap
import threading
import time
from typing import Any
from uuid import uuid4

from mtp import Agent, JsonSessionStore, SessionRecord
from mtp.providers import OpenAI
from mtp.toolkits.local import register_local_toolkits
from . import tui_codex_backend as codex_backend
from . import tui_mtp_backend as mtp_backend
from .tui_provider_factory import ProviderSelection, build_tui_provider, SUPPORTED_TUI_PROVIDERS
from .tui_settings import (
    provider_settings_path,
    load_provider_settings,
    save_provider_settings,
    ensure_provider_entry,
    is_provider_configured,
    add_custom_model,
    get_provider_models,
    preferred_model_for_provider,
    set_provider_api_key,
    delete_provider_api_key,
    list_configured_providers,
    DEFAULT_PROVIDER_MODELS,
)

# ── Modular imports (theme, toast, completers) ───────────────────────────────
from .tui_theme import (
    # Capability flags
    UNICODE_ENABLED as _UNICODE_ENABLED,
    COLOR_ENABLED as _COLOR_ENABLED,
    # Core escape sequences
    RESET, BOLD, DIM, ITALIC, UNDERLINE,
    # Semantic palette
    C_BRAND, C_BRAND_BOLD, C_ACCENT, C_ACCENT_DIM,
    C_SUCCESS, C_WARNING, C_ERROR, C_DIM, C_TEXT, C_LABEL,
    C_HIGHLIGHT, C_MODEL, C_CMD, C_KEY, C_VALUE,
    C_BORDER, C_PROMPT_ARROW, C_RESPONSE,
    # Symbols (Unicode with ASCII fallbacks)
    SYM_RULE as _SYM_RULE, SYM_V as _SYM_V,
    SYM_TL as _SYM_TL, SYM_TR as _SYM_TR,
    SYM_BL as _SYM_BL, SYM_BR as _SYM_BR,
    SYM_ML as _SYM_ML, SYM_MR as _SYM_MR,
    SYM_DOT as _SYM_DOT, SYM_FILLED as _SYM_FILLED,
    SYM_EMPTY as _SYM_EMPTY, SYM_PROMPT_ARROW as _SYM_PROMPT_ARROW,
    SYM_INFO as _SYM_INFO, SYM_WARN as _SYM_WARN,
    SYM_OK as _SYM_OK, SYM_ERR as _SYM_ERR,
    SYM_BULLET as _SYM_BULLET,
    # Drawing primitives
    get_term_width as _get_term_width,
    strip_ansi as _strip_ansi,
    hrule as _hrule,
    centered as _centered,
    box_line as _box_line,
    box_top as _box_top,
    box_bottom as _box_bottom,
    box_separator as _box_separator,
    shorten_text as _shorten_text,
    input_box_top as _input_box_top,
    input_box_bottom as _input_box_bottom,
    # Color helpers (used in logo rendering)
    _fg_rgb, _bg_rgb,
)
from .tui_toast import toast as _toast
from .tui_completers import (
    HAS_PROMPT_TOOLKIT as _HAS_PROMPT_TOOLKIT,
    build_prompt_session as _build_prompt_session,
    build_prompt_prefix_html as _build_prompt_prefix_html,
    build_prompt_prefix_html_with_box as _build_prompt_prefix_html_with_box,
    build_bottom_toolbar as _build_bottom_toolbar,
)
try:
    from prompt_toolkit.formatted_text import HTML
except ImportError:
    HTML = None  # type: ignore[assignment, misc]


_BACKENDS = {"codex", "openai", "groq", "claude", "gemini", "openrouter", "mistral", "cohere", "sambanova", "cerebras", "deepseek", "togetherai", "fireworksai"}
_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")
_MAX_ATTACHMENTS = 8
_MAX_ATTACHMENT_CHARS = 16000

_MODEL_PRESETS: list[tuple[str, str]] = [
    ("gpt-5.4", "Frontier general coding model"),
    ("gpt-5.4-mini", "Faster/cheaper coding model"),
    ("gpt-5.3-codex", "Codex-optimized coding model"),
    ("gpt-5.2", "Previous frontier model"),
]

_MODEL_SHORTCUTS = {
    "1": "gpt-5.4",
    "2": "gpt-5.4-mini",
    "3": "gpt-5.3-codex",
    "4": "gpt-5.2",
}

_REASONING_SHORTCUTS = {
    "0": "none",
    "1": "low",
    "2": "medium",
    "3": "high",
    "4": "xhigh",
}

_REASONING_NOTES = {
    "gpt-5.4": "none, low, medium, high, xhigh",
    "gpt-5.3-codex": "low, medium, high, xhigh",
}

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


# ── ASCII Art Logo ───────────────────────────────────────────────────────────

if _UNICODE_ENABLED:
    _LOGO_LINES = [
        r"  ███╗   ███╗ ████████╗ ██████╗  ",
        r"  ████╗ ████║ ╚══██╔══╝ ██╔══██╗ ",
        r"  ██╔████╔██║    ██║    ██████╔╝ ",
        r"  ██║╚██╔╝██║    ██║    ██╔═══╝  ",
        r"  ██║ ╚═╝ ██║    ██║    ██║      ",
        r"  ╚═╝     ╚═╝    ╚═╝    ╚═╝      ",
    ]
else:
    _LOGO_LINES = [
        r"  __  __ _____ ____   ",
        r" |  \/  |_   _|  _ \  ",
        r" | |\/| | | | | |_) | ",
        r" | |  | | | | |  __/  ",
        r" |_|  |_| |_| |_|     ",
    ]

# Gradient colors for the logo: purple → cyan → mint
_LOGO_GRADIENT = [
    (167, 139, 250),  # violet
    (149, 150, 252),  # blue-violet
    (129, 170, 248),  # periwinkle
    (110, 190, 244),  # sky
    (90,  210, 235),  # cyan
    (70,  225, 200),  # mint
]


def _render_logo() -> list[str]:
    """Render the ASCII logo with a vertical gradient."""
    lines = []
    for idx, raw_line in enumerate(_LOGO_LINES):
        r, g, b = _LOGO_GRADIENT[idx % len(_LOGO_GRADIENT)]
        colored = f"{BOLD}{_fg_rgb(r, g, b)}{raw_line}{RESET}"
        lines.append(_centered(colored))
    return lines


def _render_logo_gradient_sweep() -> list[str]:
    """Render the ASCII logo with a horizontal gradient sweep per line."""
    lines = []
    for idx, raw_line in enumerate(_LOGO_LINES):
        rendered = ""
        for char_idx, ch in enumerate(raw_line):
            t = char_idx / max(1, len(raw_line) - 1)
            r = int(167 + t * (70 - 167))
            g = int(139 + t * (225 - 139))
            b = int(250 + t * (200 - 250))
            rendered += f"\033[1;38;2;{r};{g};{b}m{ch}"
        rendered += RESET
        lines.append(_centered(rendered))
    return lines


def _animate_boot(state: TUIState) -> None:
    """Animated boot: sweep logo + stagger-reveal sections."""
    from mtp import __version__
    w = _get_term_width()
    active_model = state.codex_model if state.backend == "codex" else state.openai_model

    print()
    # Animate logo line-by-line with horizontal gradient sweep
    for idx, raw_line in enumerate(_LOGO_LINES):
        rendered = ""
        for char_idx, ch in enumerate(raw_line):
            t = char_idx / max(1, len(raw_line) - 1)
            r = int(167 + t * (70 - 167))
            g = int(139 + t * (225 - 139))
            b = int(250 + t * (200 - 250))
            rendered += f"\033[1;38;2;{r};{g};{b}m{ch}"
        rendered += RESET
        sys.stdout.write(_centered(rendered) + "\n")
        sys.stdout.flush()
        time.sleep(0.025)

    # Stagger-reveal the info sections
    info_lines = _build_compact_info(state, __version__, active_model, w)
    for line in info_lines:
        # Quick fade: dim → normal
        sys.stdout.write(f"\r{DIM}{line}{RESET}")
        sys.stdout.flush()
        time.sleep(0.015)
        sys.stdout.write(f"\r{line}")
        sys.stdout.flush()
        sys.stdout.write("\n")
    sys.stdout.flush()


def _build_compact_info(state: TUIState, version: str, active_model: str | None, w: int) -> list[str]:
    """Build compact info lines for banner/boot."""
    lines: list[str] = []

    # Tagline
    tagline = (
        f"{C_DIM}Model Tool Protocol{RESET}  {C_BRAND_BOLD}v{version}{RESET}  "
        f"{C_DIM}{_SYM_DOT}  SDK + Codex CLI bridge{RESET}"
    )
    lines.append(_centered(tagline))
    lines.append("")  # single blank

    # Thin rule
    rule_w = min(60, w - 4)
    rule_pad = max(0, (w - rule_w) // 2)
    lines.append(f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}")
    lines.append("")  # single blank

    # Model + Reasoning — compact inline
    model_display = active_model or "(default)"
    model_line = f"  {C_LABEL}model{RESET}  {C_MODEL}{model_display}{RESET}"
    # Add model dots
    for idx, (m, _desc) in enumerate(_MODEL_PRESETS, start=1):
        selected = m in {state.codex_model, state.openai_model}
        dot = f"{C_SUCCESS}{_SYM_FILLED}{RESET}" if selected else f"{C_DIM}{_SYM_EMPTY}{RESET}"
        model_line += f"  {dot}"
    model_line += f"    {C_LABEL}reasoning{RESET}  {C_VALUE}{state.reasoning_effort}{RESET}"
    model_line += f"    {C_LABEL}backend{RESET}  {C_ACCENT}{state.backend}{RESET}"
    lines.append(model_line)

    # Session — compact single line
    sid_short = state.session_id.split("-")[-1][:8]
    session_line = f"  {C_DIM}session{RESET} {C_VALUE}{sid_short}{RESET}"
    session_line += f"  {C_DIM}cwd{RESET} {C_TEXT}{state.cwd}{RESET}"
    session_line += f"  {C_DIM}turns{RESET} {C_VALUE}{len(state.transcript)}{RESET}"
    lines.append(session_line)

    lines.append("")  # single blank

    # Tips — compact
    lines.append(
        f"  {C_DIM}type{RESET} {C_CMD}/help{RESET} {C_DIM}for commands  "
        f"{_SYM_DOT}{RESET}  {C_ACCENT}@file{RESET} {C_DIM}to attach  "
        f"{_SYM_DOT}{RESET}  {C_CMD}/model 1-4{RESET} {C_DIM}to switch{RESET}"
    )

    lines.append("")  # single blank
    # Bottom thin rule
    lines.append(f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}")
    lines.append("")  # trailing blank
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChatResult:
    text: str
    tool_events: list[str]
    attachments: list[str]
    warnings: list[str]
    usage_lines: list[str]


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
    last_warnings: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Banner / Welcome Screen
# ─────────────────────────────────────────────────────────────────────────────

def _print_banner(state: TUIState) -> None:
    from mtp import __version__
    active_model = state.codex_model if state.backend == "codex" else state.openai_model
    w = _get_term_width()

    print()
    # Gradient sweep logo (static for /clear redraw)
    for line in _render_logo_gradient_sweep():
        print(line)

    # Compact info
    for line in _build_compact_info(state, __version__, active_model, w):
        print(line)


# ─────────────────────────────────────────────────────────────────────────────
# Help Display
# ─────────────────────────────────────────────────────────────────────────────

def _print_help() -> None:
    w = _get_term_width()
    rule_w = min(60, w - 4)
    rule_pad = max(0, (w - rule_w) // 2)
    thin_rule = f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}"
    print()
    print(f"  {C_BRAND_BOLD}Command Reference{RESET}")
    print(thin_rule)

    sections = [
        ("Navigation", [
            ("/help", "Show this command reference"),
            ("/exit", "Exit TUI"),
            ("/compose", "Open multi-line compose mode"),
            ("/status", "Show current session state"),
            ("/new [label]", "Start a fresh chat session"),
            ("/load <session_id>", "Load a saved chat session"),
            ("/open <session_id>", "View a session (read-only, non-destructive)"),
            ("/sessions", "List saved chat sessions"),
            ("/history [n]", "Show recent turns in this chat"),
            ("/clear", "Clear the terminal and redraw the banner"),
            ("/cd <dir>", "Change working directory"),
            ("/tools", "Show all tool calls from the last turn"),
        ]),
        ("Backend & Model", [
            ("/backend", "List all available providers"),
            ("/backend <provider>", "Switch to provider (codex, openai, groq, claude, etc.)"),
            ("/apikey", "List all API keys (masked)"),
            ("/apikey set <provider> <key>", "Set/update API key for provider"),
            ("/apikey delete <provider>", "Delete API key for provider"),
            ("/apikey show <provider>", "Show full API key (use with caution)"),
            ("/models", "Show all models for all providers"),
            ("/model <name>", "Switch to model"),
            ("/model add <provider> <name>", "Add custom model to any provider"),
            ("/reasoning <none|low|...|xhigh>", "Set reasoning effort (codex only)"),
            ("/rounds <n>", "Set max_rounds (MTP providers)"),
            ("/sandbox [mode]", "Set Codex sandbox mode (Ctrl+W) - controls file access permissions"),
        ]),
        ("Research & Auth", [
            ("/autoresearch on|off", "Toggle autoresearch (MTP providers)"),
            ("/research <text>", "Set research instructions"),
            ("/codex-login", "Run official codex login flow"),
        ]),
    ]

    for section_name, commands in sections:
        print(f"\n  {C_LABEL}{section_name}{RESET}")
        for cmd, desc in commands:
            cmd_str = f"{C_CMD}{cmd}{RESET}"
            cmd_vis = len(_strip_ansi(cmd_str))
            pad = max(1, 36 - cmd_vis)
            print(f"    {cmd_str}{' ' * pad}{C_DIM}{desc}{RESET}")

    print()
    print(thin_rule)
    print(f"  {C_LABEL}Prompt UX{RESET}")
    print(f"    {C_DIM}Type{RESET} {C_ACCENT}@path/to/file{RESET} {C_DIM}in a prompt to attach file context (Tab for suggestions).{RESET}")
    print(f"    {C_DIM}Example:{RESET} {C_TEXT}explain bug in @src/mtp/cli/tui.py and propose patch{RESET}")
    print()
    print(thin_rule)
    print(f"  {C_LABEL}Keyboard Shortcuts{RESET}")
    shortcuts = [
        ("Ctrl+C", "Interrupt current request / Exit on double press"),
        ("Ctrl+L", "Clear screen and redraw banner"),
        ("Ctrl+D", "Exit TUI"),
        ("Ctrl+W", "Cycle Codex sandbox mode (read-only → workspace-write → danger-full-access)"),
        ("Ctrl+R", "Reverse search through history"),
        ("Tab", "Accept autocomplete suggestion"),
        ("↑ / ↓", "Navigate command history"),
    ]
    for key, desc in shortcuts:
        key_str = f"{C_KEY}{key}{RESET}"
        key_vis = len(_strip_ansi(key_str))
        pad = max(1, 20 - key_vis)
        print(f"    {key_str}{' ' * pad}{C_DIM}{desc}{RESET}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Model Matrix Display
# ─────────────────────────────────────────────────────────────────────────────

def _print_model_matrix(state: TUIState) -> None:
    """Display all models organized by provider."""
    w = _get_term_width()
    rule_w = min(70, w - 4)
    rule_pad = max(0, (w - rule_w) // 2)
    thin_rule = f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}"
    
    print()
    print(f"  {C_BRAND_BOLD}Available Models{RESET}")
    print(thin_rule)
    
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    
    # Get current active model
    active_model = _active_model_name(state)
    
    # Codex models
    print(f"\n  {C_LABEL}Codex Models{RESET}")
    for idx, (model, note) in enumerate(_MODEL_PRESETS, start=1):
        is_active = (state.backend == "codex" and model == state.codex_model)
        marker = f"{C_SUCCESS}●{RESET}" if is_active else f"{C_DIM}○{RESET}"
        shortcut = f"{C_KEY}[{idx}]{RESET}"
        model_name = f"{C_MODEL}{model}{RESET}"
        desc = f"{C_DIM}{note}{RESET}"
        sel_tag = f"  {C_SUCCESS}← active{RESET}" if is_active else ""
        print(f"    {marker} {shortcut} {model_name}  {desc}{sel_tag}")
    
    # MTP Provider models
    for provider_name in SUPPORTED_TUI_PROVIDERS:
        models = get_provider_models(settings, provider_name)
        current_model = preferred_model_for_provider(settings, provider_name)
        
        print(f"\n  {C_LABEL}{provider_name.title()} Models{RESET}")
        for model in models:
            is_active = (state.backend == provider_name and model == current_model)
            marker = f"{C_SUCCESS}●{RESET}" if is_active else f"{C_DIM}○{RESET}"
            model_name = f"{C_MODEL}{model}{RESET}"
            sel_tag = f"  {C_SUCCESS}← active{RESET}" if is_active else ""
            print(f"    {marker} {model_name}{sel_tag}")
    
    print()
    print(f"  {C_LABEL}Reasoning Levels{RESET} {C_DIM}(Codex only){RESET}")
    reasoning_items = []
    for num, name in _REASONING_SHORTCUTS.items():
        if name == state.reasoning_effort:
            reasoning_items.append(f"{C_KEY}[{num}]{RESET} {C_SUCCESS}{name}{RESET}")
        else:
            reasoning_items.append(f"{C_KEY}[{num}]{RESET} {C_DIM}{name}{RESET}")
    print(f"      {'   '.join(reasoning_items)}")

    print()
    print(f"  {C_LABEL}Model-Specific Notes{RESET}")
    for model_key, notes in _REASONING_NOTES.items():
        print(f"    {C_MODEL}{model_key}{RESET} {C_DIM}→{RESET} {C_VALUE}{notes}{RESET}")
    print()
    print(f"  {C_DIM}Usage:{RESET}")
    print(f"    {C_CMD}/model <name>{RESET}              {C_DIM}Switch to model{RESET}")
    print(f"    {C_CMD}/model add <provider> <name>{RESET}  {C_DIM}Add custom model{RESET}")
    print(f"    {C_CMD}/reasoning <level>{RESET}          {C_DIM}Set reasoning (Codex only){RESET}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Codex detection + plumbing (unchanged logic)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_codex_bin() -> str | None:
    for candidate in ("codex.cmd", "codex"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _new_session_id() -> str:
    return f"chat-{uuid4().hex[:10]}"


def _now_label() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _active_model_name(state: TUIState) -> str:
    if state.backend == "codex":
        return state.codex_model or "(codex-default)"
    
    # MTP Provider
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    return preferred_model_for_provider(settings, state.backend)


def _serialize_transcript(turns: list[TranscriptTurn]) -> list[dict[str, Any]]:
    return [
        {
            "prompt": turn.prompt,
            "response": turn.response,
            "backend": turn.backend,
            "model": turn.model,
            "attachments": list(turn.attachments),
            "warnings": list(turn.warnings),
            "usage_lines": list(turn.usage_lines),
            "created_at": turn.created_at,
        }
        for turn in turns
    ]


def _deserialize_transcript(payload: Any) -> list[TranscriptTurn]:
    if not isinstance(payload, list):
        return []
    transcript: list[TranscriptTurn] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        transcript.append(
            TranscriptTurn(
                prompt=str(item.get("prompt") or ""),
                response=str(item.get("response") or ""),
                backend=str(item.get("backend") or "codex"),
                model=str(item.get("model") or ""),
                attachments=[str(x) for x in item.get("attachments") or []],
                warnings=[str(x) for x in item.get("warnings") or []],
                usage_lines=[str(x) for x in item.get("usage_lines") or []],
                created_at=str(item.get("created_at") or _now_label()),
            )
        )
    return transcript


def _load_session_record(state: TUIState, session_id: str) -> SessionRecord | None:
    return state.session_store.get_session(session_id=session_id, user_id=state.user_id)


def _save_tui_session(state: TUIState) -> None:
    existing = _load_session_record(state, state.session_id)
    metadata = dict(existing.metadata if existing else {})
    metadata["tui"] = {
        "session_label": state.session_label,
        "backend": state.backend,
        "cwd": str(state.cwd),
        "codex_model": state.codex_model,
        "openai_model": state.openai_model,
        "codex_session_id": state.codex_session_id,
        "reasoning_effort": state.reasoning_effort,
        "codex_sandbox_mode": state.codex_sandbox_mode,
        "max_rounds": state.max_rounds,
        "autoresearch": state.autoresearch,
        "research_instructions": state.research_instructions,
        "last_usage_lines": list(state.last_usage_lines),
        "turn_count": len(state.transcript),
        "updated_at": _now_label(),
        "transcript": _serialize_transcript(state.transcript),
    }
    record = SessionRecord(
        session_id=state.session_id,
        user_id=state.user_id or (existing.user_id if existing else None),
        metadata=metadata,
        messages=list(existing.messages) if existing else [],
        runs=list(existing.runs) if existing else [],
        created_at=existing.created_at if existing else _now_label(),
        updated_at=existing.updated_at if existing else _now_label(),
    )
    state.session_store.upsert_session(record)


def _reset_chat(
    state: TUIState,
    *,
    new_session: bool,
    session_label: str | None = None,
    preserve_settings: bool = True,
) -> None:
    if new_session:
        state.session_id = _new_session_id()
    state.session_label = session_label
    state.transcript = []
    state.last_usage_lines = []
    state.agent = None
    state.codex_session_id = None
    if not preserve_settings:
        state.backend = "codex"
        state.codex_model = "gpt-5.3-codex"
        state.openai_model = "gpt-5.4-mini"
        state.max_rounds = 6
        state.autoresearch = False
        state.research_instructions = None
        state.reasoning_effort = "medium"
    _save_tui_session(state)


def _record_turn(state: TUIState, prompt: str, result: ChatResult) -> None:
    state.transcript.append(
        TranscriptTurn(
            prompt=prompt,
            response=result.text,
            backend=state.backend,
            model=_active_model_name(state),
            attachments=list(result.attachments),
            warnings=list(result.warnings),
            usage_lines=list(result.usage_lines),
            created_at=_now_label(),
        )
    )
    state.last_usage_lines = list(result.usage_lines)
    _save_tui_session(state)


def _load_session_into_state(state: TUIState, record: SessionRecord) -> None:
    tui_meta = record.metadata.get("tui") if isinstance(record.metadata, dict) else {}
    tui_meta = tui_meta if isinstance(tui_meta, dict) else {}
    state.session_id = record.session_id
    state.session_label = (
        str(tui_meta.get("session_label"))
        if tui_meta.get("session_label") not in {None, ""}
        else None
    )
    state.transcript = _deserialize_transcript(tui_meta.get("transcript"))
    state.last_usage_lines = [str(x) for x in tui_meta.get("last_usage_lines") or []]
    backend = str(tui_meta.get("backend") or state.backend)
    if backend in _BACKENDS:
        state.backend = backend
    codex_model = tui_meta.get("codex_model")
    openai_model = tui_meta.get("openai_model")
    if isinstance(codex_model, str) or codex_model is None:
        state.codex_model = codex_model
    if isinstance(openai_model, str) and openai_model:
        state.openai_model = openai_model
    codex_session_id = tui_meta.get("codex_session_id")
    if isinstance(codex_session_id, str) and codex_session_id.strip():
        state.codex_session_id = codex_session_id.strip()
    else:
        state.codex_session_id = None
    cwd_raw = tui_meta.get("cwd")
    if isinstance(cwd_raw, str):
        candidate = Path(cwd_raw).expanduser()
        if candidate.exists() and candidate.is_dir():
            state.cwd = candidate.resolve()
    rounds = tui_meta.get("max_rounds")
    if isinstance(rounds, int) and rounds >= 1:
        state.max_rounds = rounds
    reasoning = tui_meta.get("reasoning_effort")
    if isinstance(reasoning, str) and _resolve_reasoning(reasoning) is not None:
        state.reasoning_effort = reasoning
    # Restore codex_sandbox_mode (default to workspace-write if not present for backward compatibility)
    codex_sandbox_mode = tui_meta.get("codex_sandbox_mode")
    if isinstance(codex_sandbox_mode, str) and codex_sandbox_mode in ("read-only", "workspace-write", "danger-full-access"):
        state.codex_sandbox_mode = codex_sandbox_mode
    else:
        # Backward compatibility: check old codex_write_mode field
        codex_write_mode = tui_meta.get("codex_write_mode")
        if isinstance(codex_write_mode, bool):
            state.codex_sandbox_mode = "workspace-write" if codex_write_mode else "read-only"
        else:
            state.codex_sandbox_mode = "workspace-write"  # Default to workspace-write mode
    state.autoresearch = bool(tui_meta.get("autoresearch", state.autoresearch))
    research_instructions = tui_meta.get("research_instructions")
    state.research_instructions = (
        str(research_instructions) if isinstance(research_instructions, str) and research_instructions else None
    )
    state.agent = None


def _list_saved_sessions(store: JsonSessionStore) -> list[SessionRecord]:
    if not store.file_path.exists():
        return []
    try:
        rows = json.loads(store.file_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    sessions: list[SessionRecord] = []
    for row in rows:
        if isinstance(row, dict):
            sessions.append(SessionRecord.from_dict(row))
    sessions.sort(key=lambda item: item.updated_at, reverse=True)
    return sessions


def _find_codex_session_file(thread_id: str) -> Path | None:
    """
    Search for Codex CLI session file by thread ID.
    
    Codex stores sessions in ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
    """
    from datetime import datetime, timedelta
    
    codex_home = Path.home() / ".codex" / "sessions"
    
    if not codex_home.exists():
        return None
    
    # Search recent sessions (last 30 days)
    for days_ago in range(30):
        date = datetime.now() - timedelta(days=days_ago)
        date_path = codex_home / date.strftime("%Y/%m/%d")
        
        if not date_path.exists():
            continue
        
        # Search JSONL files
        for session_file in date_path.glob("rollout-*.jsonl"):
            # Check if thread_id is in filename
            if thread_id in session_file.name:
                return session_file
            
            # Check first few lines of JSONL for thread_id
            try:
                with session_file.open("r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f):
                        if i > 20:  # Only check first 20 lines
                            break
                        if thread_id in line:
                            return session_file
            except Exception:
                continue
    
    return None


def _try_codex_thread_resume(state: TUIState, thread_id: str) -> tuple[bool, str | None]:
    """
    Test if a Codex thread ID is valid and resumable.
    
    Returns:
        (is_valid, error_message)
    """
    codex_bin = state.codex_bin or codex_backend.detect_codex_bin()
    if not codex_bin:
        return False, "Codex CLI not found"
    
    # Check if session file exists in Codex's local storage
    session_file = _find_codex_session_file(thread_id)
    if session_file is None:
        return False, "Thread not found in Codex session storage"
    
    # File exists, assume thread is valid
    # We'll verify on actual use
    return True, None


def _find_session_by_partial_id(store: JsonSessionStore, partial_id: str) -> SessionRecord | None:
    """
    Find session by partial ID match (fuzzy search).
    """
    sessions = _list_saved_sessions(store)
    
    # Try exact suffix match first
    for session in sessions:
        if session.session_id.endswith(partial_id):
            return session
    
    # Try contains match
    for session in sessions:
        if partial_id in session.session_id:
            return session
    
    # Try matching last part after dash
    for session in sessions:
        parts = session.session_id.split("-")
        if len(parts) > 1 and partial_id in parts[-1]:
            return session
    
    return None


def _create_mtp_session_from_codex_thread(state: TUIState, thread_id: str) -> SessionRecord | None:
    """
    Create an MTP session wrapper for an existing Codex thread.
    
    This allows loading Codex threads created outside MTP TUI.
    """
    session_file = _find_codex_session_file(thread_id)
    if session_file is None:
        return None
    
    # Parse Codex session file to extract conversation
    transcript: list[TranscriptTurn] = []
    
    try:
        with session_file.open("r", encoding="utf-8", errors="ignore") as f:
            user_msg = None
            assistant_msg = None
            
            for line in f:
                try:
                    event = json.loads(line.strip())
                    event_type = str(event.get("type", "")).lower()
                    
                    # Extract user messages
                    if "user" in event_type or "input" in event_type:
                        content = event.get("content") or event.get("text") or event.get("message")
                        if isinstance(content, str) and content.strip():
                            user_msg = content.strip()
                    
                    # Extract assistant messages
                    if "assistant" in event_type or "response" in event_type or "output" in event_type:
                        content = event.get("content") or event.get("text") or event.get("message")
                        if isinstance(content, str) and content.strip():
                            assistant_msg = content.strip()
                    
                    # When we have both, create a turn
                    if user_msg and assistant_msg:
                        transcript.append(
                            TranscriptTurn(
                                prompt=user_msg,
                                response=assistant_msg,
                                backend="codex",
                                model=state.codex_model or "gpt-5.3-codex",
                                attachments=[],
                                warnings=[],
                                usage_lines=[],
                                created_at=_now_label(),
                            )
                        )
                        user_msg = None
                        assistant_msg = None
                
                except json.JSONDecodeError:
                    continue
    
    except Exception as e:
        print(f"Warning: Failed to parse Codex session file: {e}")
        return None
    
    # Create MTP session record
    new_session_id = _new_session_id()
    
    record = SessionRecord(
        session_id=new_session_id,
        user_id=state.user_id,
        metadata={
            "tui": {
                "session_label": f"Imported from Codex thread {thread_id[:8]}",
                "backend": "codex",
                "cwd": str(state.cwd),
                "codex_model": state.codex_model,
                "openai_model": state.openai_model,
                "codex_session_id": thread_id,
                "reasoning_effort": state.reasoning_effort,
                "max_rounds": state.max_rounds,
                "autoresearch": state.autoresearch,
                "research_instructions": state.research_instructions,
                "last_usage_lines": [],
                "turn_count": len(transcript),
                "updated_at": _now_label(),
                "transcript": _serialize_transcript(transcript),
                "imported_from_codex": True,
                "original_thread_id": thread_id,
            }
        },
        messages=[],
        runs=[],
        created_at=_now_label(),
        updated_at=_now_label(),
    )
    
    # Save to store
    state.session_store.upsert_session(record)
    
    return record


def _open_session_viewer(state: TUIState, session_id_input: str) -> str:
    """
    Open a session in read-only mode for viewing.
    
    This is NON-DESTRUCTIVE - current session state is preserved.
    User can view past conversations without switching sessions.
    """
    
    # Use hierarchical resolution to find session
    # But we need to do it manually to avoid loading into state
    
    session_id_input = session_id_input.strip()
    found_record: SessionRecord | None = None
    source_type = "unknown"
    
    # STEP 1: Try as Codex thread ID
    thread_candidates = [
        session_id_input,
        f"thread-{session_id_input}",
        f"thread_{session_id_input}",
    ]
    
    for thread_id in thread_candidates:
        is_valid, _ = _try_codex_thread_resume(state, thread_id)
        if is_valid:
            # Check if we have an MTP session for this thread
            all_sessions = _list_saved_sessions(state.session_store)
            for session in all_sessions:
                tui_meta = session.metadata.get("tui", {}) if isinstance(session.metadata, dict) else {}
                if isinstance(tui_meta, dict):
                    stored_thread_id = tui_meta.get("codex_session_id")
                    if stored_thread_id == thread_id:
                        found_record = session
                        source_type = "codex_thread"
                        break
            
            if found_record:
                break
            
            # Create temporary session for viewing
            found_record = _create_mtp_session_from_codex_thread(state, thread_id)
            if found_record:
                source_type = "codex_thread_imported"
                break
    
    # STEP 2: Try as MTP session ID (exact match)
    if found_record is None:
        found_record = _load_session_record(state, session_id_input)
        if found_record:
            source_type = "mtp_exact"
    
    # STEP 3: Try fuzzy match
    if found_record is None:
        found_record = _find_session_by_partial_id(state.session_store, session_id_input)
        if found_record:
            source_type = "mtp_fuzzy"
    
    # STEP 4: Not found
    if found_record is None:
        sessions = _list_saved_sessions(state.session_store)
        if not sessions:
            return (
                f"{C_ERROR}{_SYM_ERR} No sessions found.{RESET}\n"
                f"  {C_DIM}Try: {C_CMD}/sessions{RESET} {C_DIM}to list available sessions{RESET}"
            )
        
        recent = sessions[:5]
        suggestions = []
        for s in recent:
            sid_short = s.session_id.split("-")[-1][:8]
            tui_meta = s.metadata.get("tui", {}) if isinstance(s.metadata, dict) else {}
            label = tui_meta.get("session_label", "(unnamed)") if isinstance(tui_meta, dict) else "(unnamed)"
            turn_count = tui_meta.get("turn_count", 0) if isinstance(tui_meta, dict) else 0
            suggestions.append(f"    {C_VALUE}{sid_short}{RESET} - {C_TEXT}{label}{RESET} ({turn_count} turns)")
        
        return (
            f"{C_ERROR}{_SYM_ERR} Session not found:{RESET} {session_id_input}\n\n"
            f"  {C_LABEL}Recent sessions:{RESET}\n" +
            "\n".join(suggestions) +
            f"\n\n  {C_DIM}Try: {C_CMD}/sessions{RESET} {C_DIM}to see all sessions{RESET}"
        )
    
    # Found session - display it
    return _display_session_readonly(state, found_record, source_type)


def _display_session_readonly(state: TUIState, record: SessionRecord, source_type: str) -> str:
    """
    Display a session in read-only mode.
    
    Formats and displays the session transcript without modifying current state.
    """
    w = _get_term_width()
    rule_w = min(80, w - 4)
    rule_pad = max(0, (w - rule_w) // 2)
    thin_rule = f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}"
    
    # Extract session metadata
    tui_meta = record.metadata.get("tui", {}) if isinstance(record.metadata, dict) else {}
    tui_meta = tui_meta if isinstance(tui_meta, dict) else {}
    
    session_label = tui_meta.get("session_label", "(unnamed)")
    backend = tui_meta.get("backend", "unknown")
    codex_model = tui_meta.get("codex_model", "unknown")
    openai_model = tui_meta.get("openai_model", "unknown")
    codex_session_id = tui_meta.get("codex_session_id")
    turn_count = tui_meta.get("turn_count", 0)
    created_at = record.created_at
    updated_at = tui_meta.get("updated_at", record.updated_at)
    
    # Deserialize transcript
    transcript = _deserialize_transcript(tui_meta.get("transcript", []))
    
    # Build display
    lines = []
    lines.append("")
    lines.append(thin_rule)
    lines.append(f"  {C_BRAND_BOLD}Session Viewer (Read-Only){RESET}")
    lines.append(thin_rule)
    lines.append("")
    
    # Session info
    sid_short = record.session_id.split("-")[-1][:8]
    lines.append(f"  {C_LABEL}Session ID:{RESET} {C_VALUE}{record.session_id}{RESET} ({C_DIM}{sid_short}{RESET})")
    lines.append(f"  {C_LABEL}Label:{RESET} {C_TEXT}{session_label}{RESET}")
    lines.append(f"  {C_LABEL}Backend:{RESET} {C_ACCENT}{backend}{RESET}")
    
    if backend == "codex":
        lines.append(f"  {C_LABEL}Model:{RESET} {C_MODEL}{codex_model}{RESET}")
        if codex_session_id:
            lines.append(f"  {C_LABEL}Codex Thread:{RESET} {C_VALUE}{codex_session_id[:24]}...{RESET}")
    else:
        lines.append(f"  {C_LABEL}Model:{RESET} {C_MODEL}{openai_model}{RESET}")
    
    lines.append(f"  {C_LABEL}Turns:{RESET} {C_VALUE}{len(transcript)}{RESET}")
    lines.append(f"  {C_LABEL}Created:{RESET} {C_DIM}{created_at}{RESET}")
    lines.append(f"  {C_LABEL}Updated:{RESET} {C_DIM}{updated_at}{RESET}")
    
    if source_type == "codex_thread_imported":
        lines.append(f"  {C_WARNING}⚠ Imported from Codex thread{RESET}")
    
    lines.append("")
    lines.append(thin_rule)
    
    # Display transcript
    if not transcript:
        lines.append(f"  {C_DIM}(No conversation history){RESET}")
        lines.append("")
    elif len(transcript) <= 10:
        # Short session - display all turns inline
        lines.append(f"  {C_LABEL}Conversation:{RESET}")
        lines.append("")
        
        for idx, turn in enumerate(transcript, start=1):
            # Turn header
            turn_meta = f"  {C_LABEL}Turn #{idx}{RESET} {C_DIM}({turn.created_at}){RESET}"
            lines.append(turn_meta)
            
            # User message
            user_lines = turn.prompt.split("\n")
            for i, line in enumerate(user_lines[:5]):  # Max 5 lines per message
                prefix = f"  {C_ACCENT}›{RESET} " if i == 0 else "    "
                lines.append(f"{prefix}{C_TEXT}{line}{RESET}")
            if len(user_lines) > 5:
                lines.append(f"    {C_DIM}... ({len(user_lines) - 5} more lines){RESET}")
            
            # Assistant message
            assistant_lines = turn.response.split("\n")
            for i, line in enumerate(assistant_lines[:5]):  # Max 5 lines per message
                prefix = f"  {C_SUCCESS}◂{RESET} " if i == 0 else "    "
                lines.append(f"{prefix}{C_TEXT}{line}{RESET}")
            if len(assistant_lines) > 5:
                lines.append(f"    {C_DIM}... ({len(assistant_lines) - 5} more lines){RESET}")
            
            # Tool events
            if turn.usage_lines:
                usage_summary = turn.usage_lines[0] if turn.usage_lines else ""
                lines.append(f"    {C_DIM}{usage_summary}{RESET}")
            
            lines.append("")
    else:
        # Long session - show summary and first/last turns
        lines.append(f"  {C_LABEL}Conversation Summary:{RESET}")
        lines.append(f"  {C_DIM}This session has {len(transcript)} turns. Showing first 3 and last 2.{RESET}")
        lines.append("")
        
        # First 3 turns
        for idx, turn in enumerate(transcript[:3], start=1):
            turn_meta = f"  {C_LABEL}Turn #{idx}{RESET} {C_DIM}({turn.created_at}){RESET}"
            lines.append(turn_meta)
            
            # Truncate messages
            user_preview = turn.prompt.replace("\n", " ")[:100]
            if len(turn.prompt) > 100:
                user_preview += "..."
            assistant_preview = turn.response.replace("\n", " ")[:100]
            if len(turn.response) > 100:
                assistant_preview += "..."
            
            lines.append(f"  {C_ACCENT}›{RESET} {C_TEXT}{user_preview}{RESET}")
            lines.append(f"  {C_SUCCESS}◂{RESET} {C_TEXT}{assistant_preview}{RESET}")
            lines.append("")
        
        # Middle indicator
        if len(transcript) > 5:
            lines.append(f"  {C_DIM}... ({len(transcript) - 5} more turns) ...{RESET}")
            lines.append("")
        
        # Last 2 turns
        for idx, turn in enumerate(transcript[-2:], start=len(transcript) - 1):
            turn_meta = f"  {C_LABEL}Turn #{idx}{RESET} {C_DIM}({turn.created_at}){RESET}"
            lines.append(turn_meta)
            
            user_preview = turn.prompt.replace("\n", " ")[:100]
            if len(turn.prompt) > 100:
                user_preview += "..."
            assistant_preview = turn.response.replace("\n", " ")[:100]
            if len(turn.response) > 100:
                assistant_preview += "..."
            
            lines.append(f"  {C_ACCENT}›{RESET} {C_TEXT}{user_preview}{RESET}")
            lines.append(f"  {C_SUCCESS}◂{RESET} {C_TEXT}{assistant_preview}{RESET}")
            lines.append("")
    
    lines.append(thin_rule)
    lines.append(f"  {C_DIM}Current session unchanged: {C_VALUE}{state.session_id}{RESET}")
    lines.append(f"  {C_DIM}To switch to this session, use: {C_CMD}/load {record.session_id.split('-')[-1][:8]}{RESET}")
    lines.append(thin_rule)
    lines.append("")
    
    # Print all lines
    for line in lines:
        print(line)
    
    return None  # Return None to avoid printing additional message


def _load_session_hierarchical(state: TUIState, session_id_input: str) -> str:
    """
    Hierarchical session resolution:
    1. Try as Codex thread ID (check if resumable)
    2. Try as MTP session ID (exact match)
    3. Try fuzzy match on MTP sessions
    4. Try to import from Codex session files
    5. Show helpful error with suggestions
    """
    
    # Normalize input
    session_id_input = session_id_input.strip()
    
    # STEP 1: Try as Codex thread ID
    print(f"  {C_DIM}→ Checking if '{session_id_input}' is a Codex thread...{RESET}")
    
    # Add common prefixes if missing
    thread_candidates = [
        session_id_input,
        f"thread-{session_id_input}",
        f"thread_{session_id_input}",
    ]
    
    for thread_id in thread_candidates:
        is_valid, error_msg = _try_codex_thread_resume(state, thread_id)
        if is_valid:
            print(f"  {C_SUCCESS}✓ Found Codex thread: {thread_id}{RESET}")
            
            # Check if we already have an MTP session for this thread
            all_sessions = _list_saved_sessions(state.session_store)
            for session in all_sessions:
                tui_meta = session.metadata.get("tui", {}) if isinstance(session.metadata, dict) else {}
                if isinstance(tui_meta, dict):
                    stored_thread_id = tui_meta.get("codex_session_id")
                    if stored_thread_id == thread_id:
                        print(f"  {C_SUCCESS}✓ Found existing MTP session for this thread{RESET}")
                        _load_session_into_state(state, session)
                        return (
                            f"{C_SUCCESS}{_SYM_OK}{RESET} Loaded session {C_VALUE}{state.session_id}{RESET} "
                            f"(Codex thread: {C_VALUE}{thread_id[:16]}...{RESET}) "
                            f"with {C_VALUE}{len(state.transcript)}{RESET} turns."
                        )
            
            # No existing MTP session, create one
            print(f"  {C_DIM}→ Creating MTP session wrapper for Codex thread...{RESET}")
            record = _create_mtp_session_from_codex_thread(state, thread_id)
            if record:
                _load_session_into_state(state, record)
                return (
                    f"{C_SUCCESS}{_SYM_OK}{RESET} Imported Codex thread {C_VALUE}{thread_id[:16]}...{RESET} "
                    f"as MTP session {C_VALUE}{state.session_id}{RESET} "
                    f"with {C_VALUE}{len(state.transcript)}{RESET} turns."
                )
    
    print(f"  {C_DIM}✗ Not a valid Codex thread{RESET}")
    
    # STEP 2: Try as MTP session ID (exact match)
    print(f"  {C_DIM}→ Checking MTP sessions (exact match)...{RESET}")
    record = _load_session_record(state, session_id_input)
    if record is not None:
        print(f"  {C_SUCCESS}✓ Found MTP session (exact match){RESET}")
        _load_session_into_state(state, record)
        return (
            f"{C_SUCCESS}{_SYM_OK}{RESET} Loaded session {C_VALUE}{state.session_id}{RESET} "
            f"with {C_VALUE}{len(state.transcript)}{RESET} turns."
        )
    
    print(f"  {C_DIM}✗ No exact match{RESET}")
    
    # STEP 3: Try fuzzy match on MTP sessions
    print(f"  {C_DIM}→ Trying fuzzy match on MTP sessions...{RESET}")
    record = _find_session_by_partial_id(state.session_store, session_id_input)
    if record is not None:
        print(f"  {C_SUCCESS}✓ Found MTP session (fuzzy match): {record.session_id}{RESET}")
        _load_session_into_state(state, record)
        return (
            f"{C_SUCCESS}{_SYM_OK}{RESET} Loaded session {C_VALUE}{state.session_id}{RESET} "
            f"(matched '{session_id_input}') "
            f"with {C_VALUE}{len(state.transcript)}{RESET} turns."
        )
    
    print(f"  {C_DIM}✗ No fuzzy match{RESET}")
    
    # STEP 4: Show helpful error with suggestions
    sessions = _list_saved_sessions(state.session_store)
    
    if not sessions:
        return (
            f"{C_ERROR}{_SYM_ERR} No sessions found.{RESET}\n"
            f"  {C_DIM}Session database: {state.session_store.file_path}{RESET}\n"
            f"  {C_DIM}Codex sessions: ~/.codex/sessions/{RESET}\n"
            f"  {C_DIM}Try: {C_CMD}/sessions{RESET} {C_DIM}to list available sessions{RESET}"
        )
    
    # Show recent sessions as suggestions
    recent = sessions[:5]
    suggestions = []
    for s in recent:
        sid_short = s.session_id.split("-")[-1][:8]
        tui_meta = s.metadata.get("tui", {}) if isinstance(s.metadata, dict) else {}
        label = tui_meta.get("session_label", "(unnamed)") if isinstance(tui_meta, dict) else "(unnamed)"
        turn_count = tui_meta.get("turn_count", 0) if isinstance(tui_meta, dict) else 0
        suggestions.append(f"    {C_VALUE}{sid_short}{RESET} - {C_TEXT}{label}{RESET} ({turn_count} turns)")
    
    return (
        f"{C_ERROR}{_SYM_ERR} Session not found:{RESET} {session_id_input}\n\n"
        f"  {C_LABEL}Recent sessions:{RESET}\n" +
        "\n".join(suggestions) +
        f"\n\n  {C_DIM}Try: {C_CMD}/sessions{RESET} {C_DIM}to see all sessions{RESET}"
    )


def _ensure_openai_agent(state: TUIState) -> Agent.MTPAgent:
    if state.agent is not None:
        return state.agent
    Agent.load_dotenv_if_available()
    tools = Agent.ToolRegistry()
    register_local_toolkits(tools, base_dir=state.cwd)
    provider = OpenAI(model=state.openai_model)
    state.agent = Agent.MTPAgent(
        provider=provider,
        tools=tools,
        strict_dependency_mode=True,
        autoresearch=state.autoresearch,
        research_instructions=state.research_instructions,
        stream_tool_events=True,
        stream_tool_results=False,
        session_store=state.session_store,
    )
    return state.agent


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
            try:
                return int(cleaned)
            except ValueError:
                return None
    return None


def _resolve_context_window(model: str | None) -> tuple[str, int | None]:
    model_name = (model or "").strip().lower()
    if not model_name:
        return "unknown", None
    for candidate, window in _MODEL_CONTEXT_WINDOWS.items():
        if model_name == candidate or candidate in model_name:
            return candidate, window
    return model_name, _MODEL_CONTEXT_WINDOWS.get(model_name)


def _context_usage_lines(model: str | None, request_tokens: int | None) -> list[str]:
    model_name, window = _resolve_context_window(model)
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


def _parse_codex_json_events(stdout_text: str, active_model: str | None) -> tuple[str, list[str], list[str], list[str]]:
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
        event_type = str(event.get("type", "")).strip().lower()
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

    return final_text, tool_events, warnings, usage_lines


def _shorten_text(text: str, limit: int = 160) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _emit_live_event(kind: str, message: str) -> None:
    icons = {
        "tool": f"{C_LABEL}{_SYM_BULLET}{RESET}",
        "warn": f"{C_WARNING}{_SYM_WARN}{RESET}",
        "status": f"{C_ACCENT}>{RESET}",
        "step": f"{C_VALUE}->{RESET}",
    }
    icon = icons.get(kind, f"{C_DIM}{_SYM_DOT}{RESET}")
    print(f"  {icon} {C_DIM}{message}{RESET}")


def _emit_codex_live_line(raw_line: str, emitted: dict[str, Any]) -> None:
    line = raw_line.strip()
    if not line:
        return
    if line.startswith("ERROR:"):
        _emit_live_event("warn", line)
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
            _emit_live_event("status", "assistant is drafting the response")
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
        # Emit once per tool key; re-emit only when reasoning meaningfully improves.
        if prev_reasoning is None or chosen_reasoning != prev_reasoning:
            if chosen_reasoning:
                _emit_live_event("tool", f"{tool_name}: {_shorten_text(chosen_reasoning, 180)}")
            else:
                _emit_live_event("tool", tool_name)
            tool_stream_state[key] = chosen_reasoning
        return
    if event_type.endswith("started") or event_type in {"run_started", "round_started", "plan_received"}:
        round_id = event.get("round")
        if round_id is not None:
            _emit_live_event("step", f"{event_type} (round {round_id})")
        else:
            _emit_live_event("step", event_type)
        return
    if event_type.endswith("failed") or event_type in {"error"}:
        detail = str(event.get("error") or event.get("message") or event_type)
        _emit_live_event("warn", _shorten_text(detail, 220))


def _extract_codex_tool_signal(event: dict[str, Any], event_type: str) -> tuple[str | None, str | None]:
    name, reasoning, _tool_key, _tool_phase = _extract_codex_tool_signal_details(event, event_type)
    return name, reasoning


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
            command_preview = _shorten_text(name, 140)
            reasoning = f"running shell command: {command_preview}"
        return normalized_name, reasoning, _tool_key(
            event_obj=event,
            item_obj=item,
            normalized_name=normalized_name,
            reasoning=reasoning,
        )

    if "tool" in event_type or "exec_command" in event_type or "function_call" in event_type:
        name = (
            _get_str(event.get("name"))
            or _get_str(event.get("tool_name"))
            or _get_str(event.get("toolName"))
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


def _run_codex_prompt(state: TUIState, prompt: str) -> ChatResult:
    codex_bin = state.codex_bin or codex_backend.detect_codex_bin()
    state.codex_bin = codex_bin
    if not codex_bin:
        return ChatResult(
            text=(
                "Codex CLI not found on PATH.\n"
                "Install with: npm install -g @openai/codex\n"
                "Then login with: codex login"
            ),
            tool_events=[],
            attachments=[],
            warnings=[],
            usage_lines=[],
        )
    
    # Build conversation history from transcript
    conversation_history: list[tuple[str, str]] = []
    for turn in state.transcript:
        conversation_history.append((turn.prompt, turn.response))
    
    codex_result = codex_backend.run_codex_prompt(
        codex_bin=codex_bin,
        cwd=state.cwd,
        prompt=prompt,
        model=state.codex_model,
        reasoning_effort=state.reasoning_effort,
        previous_session_id=state.codex_session_id,
        sandbox_mode=state.codex_sandbox_mode,
        conversation_history=conversation_history,  # Pass conversation history
        emit_live=_emit_live_event,
    )
    state.codex_session_id = codex_result.session_id
    return ChatResult(
        text=codex_result.text,
        tool_events=codex_result.tool_events,
        attachments=[],
        warnings=codex_result.warnings,
        usage_lines=codex_result.usage_lines,
    )


def _run_codex_login(state: TUIState) -> str:
    codex_bin = state.codex_bin or codex_backend.detect_codex_bin()
    state.codex_bin = codex_bin
    if not codex_bin:
        return "Codex CLI not found on PATH. Install: npm install -g @openai/codex"
    return_code = codex_backend.run_codex_login(codex_bin)
    if return_code == 0:
        return f"{C_SUCCESS}{_SYM_OK} Codex login completed.{RESET}"
    return f"{C_ERROR}{_SYM_ERR} Codex login exited with code {return_code}.{RESET}"


def _run_mtp_prompt(state: TUIState, prompt: str) -> ChatResult:
    """Execute a prompt using MTP provider backend."""
    # Ensure agent is initialized
    if state.agent is None:
        # Try to initialize agent
        switch_result = _switch_backend(state, state.backend)
        if "Failed" in switch_result or "Error" in switch_result:
            return ChatResult(
                text=switch_result,
                tool_events=[],
                attachments=[],
                warnings=["Agent initialization failed"],
                usage_lines=[],
            )
    
    try:
        # Run MTP agent
        mtp_result = mtp_backend.run_mtp_prompt(
            agent=state.agent,
            prompt=prompt,
            max_rounds=state.max_rounds,
            emit_live=_emit_live_event,
        )
        
        return ChatResult(
            text=mtp_result.text,
            tool_events=mtp_result.tool_events,
            attachments=[],
            warnings=mtp_result.warnings,
            usage_lines=mtp_result.usage_lines,
        )
    
    except Exception as exc:
        return ChatResult(
            text=f"Error executing MTP provider: {exc}",
            tool_events=[],
            attachments=[],
            warnings=[str(exc)],
            usage_lines=[],
        )


def _run_openai_prompt(state: TUIState, prompt: str) -> ChatResult:
    agent = _ensure_openai_agent(state)
    final_text = ""
    tool_events: list[str] = []
    warnings: list[str] = []
    totals: dict[str, int] = {}
    peak_request_tokens: int | None = None
    latest_rate_limits: dict[str, Any] | None = None

    for event in agent.run_events(
        prompt,
        max_rounds=state.max_rounds,
        stream_final=False,
        session_id=state.session_id,
        user_id=state.user_id,
        stream_tool_events=True,
        stream_tool_results=False,
    ):
        event_type = str(event.get("type", ""))
        if event_type == "run_started":
            _emit_live_event("status", "run started")
        elif event_type == "round_started":
            _emit_live_event("step", f"round {event.get('round')} started")
        elif event_type == "plan_received":
            batches = event.get("batches")
            batch_count = len(batches) if isinstance(batches, list) else "?"
            _emit_live_event("step", f"execution plan ready ({batch_count} batches)")
        if event_type == "llm_response":
            usage = event.get("usage")
            if isinstance(usage, dict):
                _merge_usage_metrics(totals, usage)
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
            rate_limits = event.get("rate_limits")
            if isinstance(rate_limits, dict):
                latest_rate_limits = rate_limits
            stage = str(event.get("stage") or "main")
            _emit_live_event("status", f"llm response received ({stage})")
        elif event_type == "tool_started":
            tool_name = str(event.get("tool_name") or "tool")
            call_id = str(event.get("call_id") or "")
            reasoning = str(event.get("reasoning") or "").strip()
            if call_id:
                tool_events.append(f"{tool_name} ({call_id})")
                if reasoning:
                    _emit_live_event("tool", f"started {tool_name} ({call_id}) reason: {_shorten_text(reasoning, 140)}")
                else:
                    _emit_live_event("tool", f"started {tool_name} ({call_id})")
            else:
                tool_events.append(tool_name)
                if reasoning:
                    _emit_live_event("tool", f"started {tool_name} reason: {_shorten_text(reasoning, 140)}")
                else:
                    _emit_live_event("tool", f"started {tool_name}")
        elif event_type == "tool_finished":
            tool_name = str(event.get("tool_name") or "tool")
            if not bool(event.get("success", True)):
                err = str(event.get("error") or "unknown tool error")
                warnings.append(f"{tool_name} failed: {err}")
                _emit_live_event("warn", f"{tool_name} failed: {_shorten_text(err, 180)}")
            else:
                output = str(event.get("output") or "")
                if output:
                    _emit_live_event("tool", f"finished {tool_name}: {_shorten_text(output, 160)}")
                else:
                    _emit_live_event("tool", f"finished {tool_name}")
        elif event_type == "run_completed":
            final_text = str(event.get("final_text") or "")
            _emit_live_event("status", "run completed")

    usage_lines = [
        "tokens(in/out/total/reasoning)="
        f"{_format_int(totals.get('input_tokens'))}/"
        f"{_format_int(totals.get('output_tokens'))}/"
        f"{_format_int(totals.get('total_tokens'))}/"
        f"{_format_int(totals.get('reasoning_tokens'))}",
    ]
    usage_lines.extend(_context_usage_lines(state.openai_model, peak_request_tokens))
    usage_lines.extend(_format_rate_lines(latest_rate_limits))
    return ChatResult(
        text=final_text or "(No final text returned.)",
        tool_events=tool_events,
        attachments=[],
        warnings=warnings,
        usage_lines=usage_lines,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Status Display
# ─────────────────────────────────────────────────────────────────────────────

def _status_lines(state: TUIState) -> list[str]:
    last_usage = " | ".join(state.last_usage_lines) if state.last_usage_lines else "(none)"
    return [
        f"session_id={state.session_id}",
        f"session_label={state.session_label or '(none)'}",
        f"turns={len(state.transcript)}",
        f"backend={state.backend}",
        f"cwd={state.cwd}",
        f"codex_session_id={state.codex_session_id or '(none)'}",
        f"codex_model={state.codex_model or '(codex-default)'}",
        f"openai_model={state.openai_model}",
        f"max_rounds={state.max_rounds}",
        f"autoresearch={state.autoresearch}",
        f"research_instructions={state.research_instructions or '(none)'}",
        f"reasoning_effort={state.reasoning_effort}",
        f"last_usage={last_usage}",
    ]


def _print_status(state: TUIState) -> None:
    w = _get_term_width()
    rule_w = min(60, w - 4)
    rule_pad = max(0, (w - rule_w) // 2)
    thin_rule = f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}"
    print()
    print(f"  {C_BRAND_BOLD}Session Status{RESET}")
    print(thin_rule)

    # Format sandbox mode display
    sandbox_mode_colors = {
        "read-only": (C_WARNING, "🔒"),
        "workspace-write": (C_SUCCESS, "✓"),
        "danger-full-access": (C_ERROR, "⚠"),
    }
    color, icon = sandbox_mode_colors.get(state.codex_sandbox_mode, (C_DIM, "?"))
    sandbox_mode_display = f"{color}{state.codex_sandbox_mode.upper()}{RESET} {icon}"

    fields = [
        ("session_id", state.session_id, C_VALUE),
        ("session_label", state.session_label or "(none)", C_DIM),
        ("turns", str(len(state.transcript)), C_VALUE),
        ("backend", state.backend, C_SUCCESS),
        ("codex_sandbox_mode", sandbox_mode_display, ""),  # Color already in display
        ("cwd", str(state.cwd), C_TEXT),
        ("codex_session", state.codex_session_id or "(none)", C_DIM),
        ("codex_model", state.codex_model or "(codex-default)", C_MODEL),
        ("openai_model", state.openai_model, C_MODEL),
        ("max_rounds", str(state.max_rounds), C_VALUE),
        ("autoresearch", str(state.autoresearch), C_VALUE),
        ("research", state.research_instructions or "(none)", C_DIM),
        ("reasoning", state.reasoning_effort, C_VALUE),
    ]
    for label, value, color in fields:
        if color:
            print(f"    {C_LABEL}{label:<20}{RESET} {color}{value}{RESET}")
        else:
            print(f"    {C_LABEL}{label:<20}{RESET} {value}")
    usage_lines = state.last_usage_lines or ["(none)"]
    print(f"    {C_LABEL}{'last_usage':<20}{RESET} {C_VALUE}{usage_lines[0]}{RESET}")
    for extra in usage_lines[1:]:
        print(f"    {C_LABEL}{'':<20}{RESET} {C_VALUE}{extra}{RESET}")
    print()


def _print_history(state: TUIState, limit: int | None = None) -> None:
    w = _get_term_width()
    rule_w = min(60, w - 4)
    rule_pad = max(0, (w - rule_w) // 2)
    thin_rule = f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}"
    turns = state.transcript[-limit:] if limit is not None else state.transcript
    print()
    print(f"  {C_BRAND_BOLD}Chat History{RESET}")
    print(thin_rule)
    if not turns:
        print(f"  {C_DIM}No turns yet in this chat.{RESET}")
        print()
        return
    for idx, turn in enumerate(turns, start=max(1, len(state.transcript) - len(turns) + 1)):
        meta = (
            f"  {C_LABEL}#{idx}{RESET} {C_DIM}{turn.created_at} "
            f"{_SYM_DOT} {turn.backend} {_SYM_DOT} {turn.model}{RESET}"
        )
        print(meta)
        prompt_preview = turn.prompt.replace("\n", " ")
        response_preview = turn.response.replace("\n", " ")
        if len(prompt_preview) > 110:
            prompt_preview = prompt_preview[:107] + "..."
        if len(response_preview) > 110:
            response_preview = response_preview[:107] + "..."
        print(f"    {C_ACCENT}›{RESET} {C_TEXT}{prompt_preview}{RESET}")
        print(f"    {C_SUCCESS}◂{RESET} {C_TEXT}{response_preview}{RESET}")
    print()


def _print_saved_sessions(state: TUIState) -> None:
    sessions = _list_saved_sessions(state.session_store)
    w = _get_term_width()
    rule_w = min(60, w - 4)
    rule_pad = max(0, (w - rule_w) // 2)
    thin_rule = f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}"
    print()
    print(f"  {C_BRAND_BOLD}Saved Sessions{RESET}")
    print(thin_rule)
    if not sessions:
        print(f"  {C_DIM}No saved sessions yet.{RESET}")
        print()
        return
    for record in sessions[:12]:
        tui_meta = record.metadata.get("tui") if isinstance(record.metadata, dict) else {}
        tui_meta = tui_meta if isinstance(tui_meta, dict) else {}
        label = str(tui_meta.get("session_label") or "(unnamed)")
        turn_count = int(tui_meta.get("turn_count") or 0)
        backend = str(tui_meta.get("backend") or "unknown")
        updated = str(tui_meta.get("updated_at") or record.updated_at)
        active = f" {C_SUCCESS}● active{RESET}" if record.session_id == state.session_id else ""
        sid_short = record.session_id.split("-")[-1][:8]
        print(f"  {C_VALUE}{sid_short}{RESET} {C_TEXT}{label}{RESET}{active}")
        print(f"    {C_DIM}{backend} {_SYM_DOT} {turn_count} turns {_SYM_DOT} {updated}{RESET}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Model/Reasoning resolution (unchanged logic)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_model(arg: str) -> str:
    normalized = arg.strip().lower()
    if normalized in _MODEL_SHORTCUTS:
        return _MODEL_SHORTCUTS[normalized]
    return arg.strip()


def _resolve_reasoning(arg: str) -> str | None:
    normalized = arg.strip().lower()
    if normalized in _REASONING_SHORTCUTS:
        return _REASONING_SHORTCUTS[normalized]
    if normalized in _REASONING_EFFORTS:
        return normalized
    if normalized in {"extra-high", "extra_high"}:
        return "xhigh"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Provider Management Functions
# ─────────────────────────────────────────────────────────────────────────────

def _print_provider_list(state: TUIState) -> None:
    """Display all available providers with their configuration status."""
    w = _get_term_width()
    rule_w = min(70, w - 4)
    rule_pad = max(0, (w - rule_w) // 2)
    thin_rule = f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}"
    
    print()
    print(f"  {C_BRAND_BOLD}Available Providers{RESET}")
    print(thin_rule)
    
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    
    # Codex (special case) - detect if not already in state
    codex_bin = state.codex_bin or codex_backend.detect_codex_bin()
    if codex_bin and not state.codex_bin:
        state.codex_bin = codex_bin  # Cache the detection
    
    is_active_codex = state.backend == "codex"
    marker_codex = f"{C_SUCCESS}●{RESET}" if is_active_codex else f"{C_DIM}○{RESET}"
    model_codex = state.codex_model or "gpt-5.4-codex"
    
    if codex_bin:
        codex_status = f"{C_SUCCESS}✓ Ready{RESET}"
    else:
        codex_status = f"{C_WARNING}⚠ Not installed{RESET}"
    
    print(f"  {marker_codex} {C_VALUE}codex{RESET:<20} {C_MODEL}{model_codex:<30}{RESET} {codex_status}")
    
    # MTP Providers
    for provider_name in SUPPORTED_TUI_PROVIDERS:
        is_configured = is_provider_configured(settings, provider_name)
        is_active = state.backend == provider_name
        
        marker = f"{C_SUCCESS}●{RESET}" if is_active else f"{C_DIM}○{RESET}"
        
        if is_configured:
            model = preferred_model_for_provider(settings, provider_name)
            status = f"{C_SUCCESS}✓ Ready{RESET}"
        else:
            model = "(not configured)"
            status = f"{C_WARNING}⚠ Setup needed{RESET}"
        
        print(f"  {marker} {C_VALUE}{provider_name:<20}{RESET} {C_MODEL}{model:<30}{RESET} {status}")
    
    print()
    print(f"  {C_DIM}Usage:{RESET} {C_CMD}/backend <provider>{RESET}")
    print()


def _setup_provider_interactive(state: TUIState, provider_name: str) -> tuple[bool, str]:
    """
    Interactive setup flow for a new provider.
    
    Returns:
        (success, message) tuple
    """
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    entry = ensure_provider_entry(settings, provider_name)
    
    print()
    print(f"  {C_BRAND_BOLD}Setup {provider_name}{RESET}")
    print(f"  {C_DIM}{'─' * 60}{RESET}")
    
    # Step 1: API Key
    if not entry.get("api_key"):
        print(f"\n  {C_LABEL}Step 1: API Key{RESET}")
        print(f"  {C_DIM}Please provide your API key for {provider_name}.{RESET}")
        print(f"  {C_WARNING}⚠ Enter the FULL API key (not a masked version){RESET}")
        
        try:
            api_key = input(f"  {C_PROMPT_ARROW}API Key:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return False, f"{C_WARNING}Setup cancelled.{RESET}"
        
        if not api_key:
            return False, f"{C_WARNING}Setup cancelled (no API key provided).{RESET}"
        
        # Warn if key looks truncated/masked
        if "..." in api_key or "*" in api_key or len(api_key) < 20:
            print(f"\n  {C_WARNING}⚠ Warning: This looks like a masked/truncated key.{RESET}")
            print(f"  {C_DIM}Please enter the FULL API key from your provider dashboard.{RESET}")
            confirm = input(f"  {C_PROMPT_ARROW}Continue anyway? (y/N):{RESET} ").strip().lower()
            if confirm not in ("y", "yes"):
                return False, f"{C_WARNING}Setup cancelled.{RESET}"
        
        entry["api_key"] = api_key
    else:
        print(f"\n  {C_SUCCESS}✓{RESET} API key already configured")
    
    # Step 2: Model Selection
    default_model = DEFAULT_PROVIDER_MODELS.get(provider_name, "default")
    print(f"\n  {C_LABEL}Step 2: Model Selection{RESET}")
    print(f"  {C_DIM}Default: {C_MODEL}{default_model}{RESET}")
    
    try:
        model = input(f"  {C_PROMPT_ARROW}Model (press Enter for default):{RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False, f"{C_WARNING}Setup cancelled.{RESET}"
    
    if not model:
        model = default_model
    
    entry["model"] = model
    
    # Step 3: Save
    save_provider_settings(settings_path, settings)
    
    print()
    print(f"  {C_SUCCESS}✓ Provider {C_VALUE}{provider_name}{RESET} configured successfully!{RESET}")
    print()
    
    return True, ""


def _switch_backend(state: TUIState, provider_name: str) -> str:
    """
    Switch to a different provider backend.
    
    Handles both Codex (external binary) and MTP providers (SDK).
    """
    provider_name = provider_name.lower().strip()
    
    # Special case: Codex
    if provider_name == "codex":
        if not state.codex_bin:
            codex_bin = codex_backend.detect_codex_bin()
            if not codex_bin:
                return (
                    f"{C_ERROR}Codex CLI not found.{RESET}\n"
                    f"  {C_DIM}Install from: https://openai.com/codex{RESET}"
                )
            state.codex_bin = codex_bin
        
        state.backend = "codex"
        state.agent = None  # Clear MTP agent
        _save_tui_session(state)
        return f"{C_SUCCESS}✓{RESET} Switched to {C_VALUE}Codex{RESET} backend."
    
    # Validate provider
    if provider_name not in SUPPORTED_TUI_PROVIDERS:
        available = ", ".join(sorted(SUPPORTED_TUI_PROVIDERS))
        return (
            f"{C_ERROR}Unknown provider: {provider_name}{RESET}\n"
            f"  {C_DIM}Available: {available}{RESET}"
        )
    
    # Check configuration
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    
    if not is_provider_configured(settings, provider_name):
        # Run interactive setup
        success, message = _setup_provider_interactive(state, provider_name)
        if not success:
            return message
        
        # Reload settings after setup
        settings = load_provider_settings(settings_path)
    
    # Get provider configuration
    entry = ensure_provider_entry(settings, provider_name)
    model = entry.get("model") or DEFAULT_PROVIDER_MODELS.get(provider_name, "default")
    api_key = entry.get("api_key")
    
    # Build provider
    try:
        selection = ProviderSelection(
            provider_name=provider_name,
            model_name=model,
            api_key=api_key,
        )
        provider = build_tui_provider(selection)
    except Exception as e:
        return f"{C_ERROR}Failed to initialize provider: {e}{RESET}"
    
    # Build MTP agent
    try:
        # Get or create tool registry
        if state.agent and hasattr(state.agent, 'tools'):
            tools = state.agent.tools
        else:
            tools = Agent.ToolRegistry()
            register_local_toolkits(tools, base_dir=str(state.cwd))
        
        agent = mtp_backend.build_mtp_agent(
            provider=provider,
            tools=tools,
            cwd=state.cwd,
            max_rounds=state.max_rounds,
            autoresearch=state.autoresearch,
            research_instructions=state.research_instructions,
            debug_mode=False,  # Can be made configurable
        )
        
        state.agent = agent
        state.backend = provider_name
        _save_tui_session(state)
        
        return (
            f"{C_SUCCESS}✓{RESET} Switched to {C_VALUE}{provider_name}{RESET} "
            f"with model {C_MODEL}{model}{RESET}."
        )
    
    except Exception as e:
        return f"{C_ERROR}Failed to create agent: {e}{RESET}"


def _print_apikey_list(state: TUIState) -> None:
    """Display all API keys with masked values."""
    w = _get_term_width()
    rule_w = min(70, w - 4)
    rule_pad = max(0, (w - rule_w) // 2)
    thin_rule = f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}"
    
    print()
    print(f"  {C_BRAND_BOLD}API Key Management{RESET}")
    print(thin_rule)
    
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    
    configured_providers = list_configured_providers(settings)
    
    # Count configured
    total = len(configured_providers)
    configured_count = sum(1 for _, has_key in configured_providers if has_key)
    
    print(f"  {C_LABEL}Status:{RESET} {C_VALUE}{configured_count}/{total}{RESET} providers configured")
    print()
    
    for provider_name, has_key in configured_providers:
        entry = ensure_provider_entry(settings, provider_name)
        api_key = entry.get("api_key")
        
        if has_key and api_key:
            # Mask the API key
            masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "*" * len(api_key)
            status = f"{C_SUCCESS}✓{RESET} {C_VALUE}{masked}{RESET}"
        else:
            status = f"{C_DIM}(not set){RESET}"
        
        print(f"  {C_LABEL}{provider_name:<15}{RESET} {status}")
    
    print()
    print(f"  {C_DIM}Commands:{RESET}")
    print(f"    {C_CMD}/apikey set <provider> <key>{RESET}     {C_DIM}Set/update API key{RESET}")
    print(f"    {C_CMD}/apikey delete <provider>{RESET}        {C_DIM}Delete API key{RESET}")
    print(f"    {C_CMD}/apikey show <provider>{RESET}          {C_DIM}Show full API key{RESET}")
    print()


def _handle_apikey_command(state: TUIState, arg: str) -> str:
    """Handle /apikey subcommands."""
    if not arg:
        # List all API keys
        _print_apikey_list(state)
        return ""
    
    parts = arg.split(None, 2)
    subcommand = parts[0].lower()
    
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    
    # /apikey set <provider> <key>
    if subcommand == "set":
        if len(parts) < 3:
            return f"{C_ERROR}Usage:{RESET} {C_CMD}/apikey set <provider> <key>{RESET}"
        
        provider_name = parts[1].lower()
        api_key = parts[2]
        
        if provider_name not in SUPPORTED_TUI_PROVIDERS:
            return f"{C_ERROR}Unknown provider: {provider_name}{RESET}"
        
        # Warn if key looks truncated/masked
        if "..." in api_key or "*" in api_key or len(api_key) < 20:
            return (
                f"{C_WARNING}⚠ Warning: This looks like a masked/truncated key.{RESET}\n"
                f"  {C_DIM}Please enter the FULL API key from your provider dashboard.{RESET}\n"
                f"  {C_DIM}Example: gsk_1234567890abcdefghijklmnopqrstuvwxyz{RESET}"
            )
        
        set_provider_api_key(settings, provider_name, api_key)
        save_provider_settings(settings_path, settings)
        
        # Force agent rebuild if this is the active provider
        if state.backend == provider_name:
            state.agent = None
        
        masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "*" * len(api_key)
        return f"{C_SUCCESS}✓{RESET} API key for {C_VALUE}{provider_name}{RESET} set to {C_VALUE}{masked}{RESET}"
    
    # /apikey delete <provider>
    elif subcommand == "delete":
        if len(parts) < 2:
            return f"{C_ERROR}Usage:{RESET} {C_CMD}/apikey delete <provider>{RESET}"
        
        provider_name = parts[1].lower()
        
        if provider_name not in SUPPORTED_TUI_PROVIDERS:
            return f"{C_ERROR}Unknown provider: {provider_name}{RESET}"
        
        deleted = delete_provider_api_key(settings, provider_name)
        save_provider_settings(settings_path, settings)
        
        # Force agent rebuild if this is the active provider
        if state.backend == provider_name:
            state.agent = None
        
        if deleted:
            return f"{C_SUCCESS}✓{RESET} API key for {C_VALUE}{provider_name}{RESET} deleted"
        else:
            return f"{C_WARNING}No API key was set for {C_VALUE}{provider_name}{RESET}"
    
    # /apikey show <provider>
    elif subcommand == "show":
        if len(parts) < 2:
            return f"{C_ERROR}Usage:{RESET} {C_CMD}/apikey show <provider>{RESET}"
        
        provider_name = parts[1].lower()
        
        if provider_name not in SUPPORTED_TUI_PROVIDERS:
            return f"{C_ERROR}Unknown provider: {provider_name}{RESET}"
        
        entry = ensure_provider_entry(settings, provider_name)
        api_key = entry.get("api_key")
        
        if api_key:
            return (
                f"{C_LABEL}{provider_name}:{RESET} {C_VALUE}{api_key}{RESET}\n"
                f"  {C_WARNING}⚠ Keep this key secure!{RESET}"
            )
        else:
            return f"{C_WARNING}No API key set for {C_VALUE}{provider_name}{RESET}"
    
    else:
        return (
            f"{C_ERROR}Unknown subcommand: {subcommand}{RESET}\n"
            f"  {C_DIM}Available: set, delete, show{RESET}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Command Handler
# ─────────────────────────────────────────────────────────────────────────────

def _handle_command(state: TUIState, raw: str) -> str | None:
    parts = raw.strip().split(" ", 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/help":
        _print_help()
        return None
    if cmd == "/compose":
        return "__compose__"
    if cmd == "/exit":
        return "__exit__"
    if cmd == "/tools":
        _print_tool_events_expanded(state)
        return None
    if cmd in {"/new", "/reset"}:
        _reset_chat(state, new_session=True, session_label=arg or None)
        label_text = f" ({arg})" if arg else ""
        return f"{C_SUCCESS}{_SYM_OK}{RESET} Started new chat {C_VALUE}{state.session_id}{RESET}{label_text}."
    if cmd == "/clear":
        os.system("cls" if os.name == "nt" else "clear")
        _print_banner(state)
        return None
    if cmd == "/status":
        _print_status(state)
        return None
    if cmd == "/history":
        limit: int | None = None
        if arg:
            if not arg.isdigit():
                return f"{C_WARNING}Usage:{RESET} {C_CMD}/history [count]{RESET}"
            limit = int(arg)
        _print_history(state, limit=limit)
        return None
    if cmd == "/sessions":
        _print_saved_sessions(state)
        return None
    if cmd == "/load":
        if not arg:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/load <session_id>{RESET}"
        
        # Hierarchical session resolution
        result = _load_session_hierarchical(state, arg)
        return result
    if cmd == "/open":
        if not arg:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/open <session_id>{RESET}"
        
        # Open session in read-only mode
        result = _open_session_viewer(state, arg)
        return result
    if cmd == "/models":
        _print_model_matrix(state)
        return None
    if cmd == "/backend":
        if not arg:
            # List all providers
            _print_provider_list(state)
            return None
        
        # Switch to provider
        return _switch_backend(state, arg)
    
    if cmd == "/apikey":
        # API key management
        result = _handle_apikey_command(state, arg)
        return result if result else None
    
    if cmd == "/model":
        if not arg:
            _print_model_matrix(state)
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/model <name>{RESET} or {C_CMD}/model add <provider> <name>{RESET}"
        
        # Handle "/model add <provider> <model-name>"
        if arg.startswith("add "):
            parts = arg[4:].strip().split(None, 1)
            if len(parts) < 2:
                return f"{C_ERROR}Usage:{RESET} {C_CMD}/model add <provider> <model-name>{RESET}"
            
            provider_name = parts[0].lower()
            model_name = parts[1].strip()
            
            # Validate provider
            if provider_name == "codex":
                return f"{C_WARNING}Cannot add custom models to Codex backend.{RESET}"
            
            if provider_name not in SUPPORTED_TUI_PROVIDERS:
                available = ", ".join(sorted(SUPPORTED_TUI_PROVIDERS))
                return (
                    f"{C_ERROR}Unknown provider: {provider_name}{RESET}\n"
                    f"  {C_DIM}Available: {available}{RESET}"
                )
            
            # Add to provider's model list
            settings_path = provider_settings_path(state.session_store.file_path)
            settings = load_provider_settings(settings_path)
            
            added = add_custom_model(settings, provider_name, model_name)
            save_provider_settings(settings_path, settings)
            
            if added:
                return f"{C_SUCCESS}✓{RESET} Added model {C_VALUE}{model_name}{RESET} to {provider_name}."
            else:
                return f"{C_WARNING}Model {C_VALUE}{model_name}{RESET} already exists in {provider_name}.{RESET}"
        
        # Handle "/model <name>" (switch model)
        resolved = _resolve_model(arg)
        
        if state.backend == "codex":
            # Codex backend
            if resolved.lower() in {"default", "auto"}:
                state.codex_model = None
                _save_tui_session(state)
                return f"{C_SUCCESS}{_SYM_OK}{RESET} Codex model reset to CLI default."
            state.codex_model = resolved
            _save_tui_session(state)
            return f"{C_SUCCESS}{_SYM_OK}{RESET} Codex model set to {C_MODEL}{resolved}{RESET}."
        else:
            # MTP Provider backend
            settings_path = provider_settings_path(state.session_store.file_path)
            settings = load_provider_settings(settings_path)
            entry = ensure_provider_entry(settings, state.backend)
            
            entry["model"] = resolved
            save_provider_settings(settings_path, settings)
            
            # Force agent rebuild on next chat
            state.agent = None
            _save_tui_session(state)
            
            return f"{C_SUCCESS}{_SYM_OK}{RESET} {state.backend} model set to {C_MODEL}{resolved}{RESET}. Agent will reload."

    if cmd == "/reasoning":
        if not arg:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/reasoning <none|low|medium|high|xhigh>{RESET}"
        resolved_reasoning = _resolve_reasoning(arg)
        if resolved_reasoning is None:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/reasoning <none|low|medium|high|xhigh>{RESET}"
        state.reasoning_effort = resolved_reasoning
        _save_tui_session(state)
        return (
            f"{C_SUCCESS}{_SYM_OK}{RESET} reasoning_effort set to {C_VALUE}{resolved_reasoning}{RESET}. "
            f"{C_DIM}Forwarded to codex backend.{RESET}"
        )
    if cmd == "/rounds":
        if not arg.isdigit():
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/rounds <positive-int>{RESET}"
        rounds = int(arg)
        if rounds < 1:
            return f"{C_ERROR}max_rounds must be >= 1{RESET}"
        state.max_rounds = rounds
        _save_tui_session(state)
        return f"{C_SUCCESS}{_SYM_OK}{RESET} max_rounds set to {C_VALUE}{rounds}{RESET}."
    if cmd == "/autoresearch":
        lowered = arg.lower()
        if lowered not in {"on", "off"}:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/autoresearch on|off{RESET}"
        state.autoresearch = lowered == "on"
        state.agent = None
        _save_tui_session(state)
        return f"{C_SUCCESS}{_SYM_OK}{RESET} autoresearch={C_VALUE}{state.autoresearch}{RESET}. Agent reloaded."
    if cmd == "/research":
        state.research_instructions = arg or None
        state.agent = None
        _save_tui_session(state)
        return f"{C_SUCCESS}{_SYM_OK}{RESET} research_instructions updated. Agent reloaded."
    if cmd == "/codex-login":
        return _run_codex_login(state)
    if cmd == "/cd":
        if not arg:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/cd <dir>{RESET}"
        target = Path(arg).expanduser().resolve()
        if not target.exists() or not target.is_dir():
            return f"{C_ERROR}{_SYM_ERR} Directory not found:{RESET} {target}"
        state.cwd = target
        state.agent = None
        _save_tui_session(state)
        return f"{C_SUCCESS}{_SYM_OK}{RESET} cwd set to {C_TEXT}{target}{RESET}. Agent reloaded."
    if cmd == "/sandbox":
        if not arg:
            # Cycle through modes: read-only → workspace-write → danger-full-access → read-only
            modes = ["read-only", "workspace-write", "danger-full-access"]
            current_idx = modes.index(state.codex_sandbox_mode) if state.codex_sandbox_mode in modes else 1
            next_idx = (current_idx + 1) % len(modes)
            state.codex_sandbox_mode = modes[next_idx]
            _save_tui_session(state)
            
            # Display with appropriate color and icon
            mode_display = {
                "read-only": (C_WARNING, "🔒", "Codex can only read files (safe mode)"),
                "workspace-write": (C_SUCCESS, "✓", "Codex can modify files in workspace"),
                "danger-full-access": (C_ERROR, "⚠", "Codex has unrestricted file access (DANGEROUS)"),
            }
            color, icon, desc = mode_display.get(state.codex_sandbox_mode, (C_DIM, "?", "Unknown mode"))
            
            return (
                f"{C_SUCCESS}{_SYM_OK}{RESET} Codex sandbox mode: {color}{state.codex_sandbox_mode.upper()}{RESET} {icon}\n"
                f"  {C_DIM}{desc}{RESET}\n"
                f"  {C_DIM}Cycle with: {C_CMD}/sandbox{RESET} {C_DIM}or {C_KEY}Ctrl+W{RESET}"
            )
        # Explicit mode setting
        lowered = arg.lower()
        if lowered not in {"read-only", "workspace-write", "danger-full-access", "readonly", "write", "full"}:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/sandbox [read-only|workspace-write|danger-full-access]{RESET}"
        
        # Normalize shortcuts
        mode_map = {
            "readonly": "read-only",
            "write": "workspace-write",
            "full": "danger-full-access",
        }
        state.codex_sandbox_mode = mode_map.get(lowered, lowered)
        _save_tui_session(state)
        
        # Display with appropriate color and icon
        mode_display = {
            "read-only": (C_WARNING, "🔒", "Codex can only read files (safe mode)"),
            "workspace-write": (C_SUCCESS, "✓", "Codex can modify files in workspace"),
            "danger-full-access": (C_ERROR, "⚠", "Codex has unrestricted file access (DANGEROUS)"),
        }
        color, icon, desc = mode_display.get(state.codex_sandbox_mode, (C_DIM, "?", "Unknown mode"))
        
        return (
            f"{C_SUCCESS}{_SYM_OK}{RESET} Codex sandbox mode: {color}{state.codex_sandbox_mode.upper()}{RESET} {icon}\n"
            f"  {C_DIM}{desc}{RESET}"
        )
    return f"{C_ERROR}Unknown command.{RESET} Use {C_CMD}/help{RESET}."


# ─────────────────────────────────────────────────────────────────────────────
# Input Normalization (unchanged logic)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_input(raw: str) -> str:
    # Support command entry without leading slash, e.g. `codex-login`.
    command_heads = {
        "help",
        "exit",
        "compose",
        "new",
        "reset",
        "clear",
        "status",
        "history",
        "sessions",
        "load",
        "backend",
        "apikey",
        "models",
        "model",
        "reasoning",
        "rounds",
        "codex-login",
        "autoresearch",
        "research",
        "cd",
        "tools",
        "sandbox",
    }
    if raw.startswith("/"):
        return raw
    head = raw.split(" ", 1)[0].strip().lower()
    if head in command_heads:
        return "/" + raw
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Attachment Handling (unchanged logic)
# ─────────────────────────────────────────────────────────────────────────────

def _token_looks_like_file_ref(token: str) -> bool:
    if token.startswith("@/"):
        return True
    path_token = token[1:]
    return any(ch in path_token for ch in ("/", "\\", ".")) and "@" not in path_token


def _collect_prompt_attachments(prompt: str, cwd: Path) -> tuple[str, list[str], list[str]]:
    attachments: list[str] = []
    warnings: list[str] = []
    appended_blocks: list[str] = []
    for token in re.findall(r"(?<!\S)@[^\s]+", prompt):
        if not _token_looks_like_file_ref(token):
            continue
        if len(attachments) >= _MAX_ATTACHMENTS:
            warnings.append(
                f"Attachment limit reached ({_MAX_ATTACHMENTS}); skipping additional @file tokens."
            )
            break
        raw_path = token[1:]
        path = Path(raw_path)
        resolved = (cwd / path).resolve() if not path.is_absolute() else path.resolve()
        if not resolved.exists():
            warnings.append(f"Attachment not found: {raw_path}")
            continue
        if not resolved.is_file():
            warnings.append(f"Attachment is not a file: {raw_path}")
            continue
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not read attachment {raw_path}: {exc}")
            continue
        if len(text) > _MAX_ATTACHMENT_CHARS:
            text = text[:_MAX_ATTACHMENT_CHARS]
            warnings.append(
                f"Attachment truncated to {_MAX_ATTACHMENT_CHARS} chars: {raw_path}"
            )
        display_path = str(resolved.relative_to(cwd)) if resolved.is_relative_to(cwd) else str(resolved)
        attachments.append(display_path)
        appended_blocks.append(
            "\n".join(
                [
                    f"[Attached file: {display_path}]",
                    "```text",
                    text,
                    "```",
                ]
            )
        )
    if not appended_blocks:
        return prompt, attachments, warnings
    expanded = f"{prompt}\n\n" + "\n\n".join(appended_blocks)
    return expanded, attachments, warnings


# ─────────────────────────────────────────────────────────────────────────────
# Spinner
# ─────────────────────────────────────────────────────────────────────────────

_SPINNER_PRESETS = {
    "reasoning": ("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", 0.06, "Reasoning"),
    "streaming": ("▏▎▍▌▋▊▉█▉▊▋▌▍▎▏", 0.05, "Streaming"),
    "tool":      ("⠿⣿⣷⣶⣦⣤⣀⣤⣦⣶", 0.08, "Running tool"),
    "search":    ("◐◓◑◒", 0.10, "Searching"),
    "default":   ("⣾⣽⣻⢿⡿⣟⣯⣷", 0.07, "Thinking"),
}


class _Spinner:
    """An animated terminal spinner with elapsed time & context-aware presets."""

    def __init__(self, label: str = "Thinking", preset: str = "default"):
        preset_data = _SPINNER_PRESETS.get(preset, _SPINNER_PRESETS["default"])
        self._frames = list(preset_data[0])
        self._delay = preset_data[1]
        self._label = label or preset_data[2]
        self._running = False
        self._thread: threading.Thread | None = None
        self._start_time: float = 0.0

    def start(self) -> None:
        self._running = True
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        # Clear the spinner line
        sys.stdout.write(f"\r{' ' * (_get_term_width())}\r")
        sys.stdout.flush()

    def _spin(self) -> None:
        idx = 0
        # Gradient colors for spinner frames
        spin_colors = [
            _fg_rgb(167, 139, 250),
            _fg_rgb(149, 155, 252),
            _fg_rgb(129, 170, 248),
            _fg_rgb(110, 195, 244),
            _fg_rgb(90,  215, 235),
            _fg_rgb(70,  230, 200),
            _fg_rgb(90,  215, 235),
            _fg_rgb(110, 195, 244),
            _fg_rgb(129, 170, 248),
            _fg_rgb(149, 155, 252),
        ]
        while self._running:
            frame = self._frames[idx % len(self._frames)]
            color = spin_colors[idx % len(spin_colors)]
            elapsed = time.monotonic() - self._start_time
            elapsed_str = f" {C_BORDER}{elapsed:.1f}s{RESET}"
            sys.stdout.write(f"\r  {color}{frame}{RESET} {C_DIM}{self._label}...{RESET}{elapsed_str}")
            sys.stdout.flush()
            idx += 1
            time.sleep(self._delay)


# ─────────────────────────────────────────────────────────────────────────────
# Response Rendering
# ─────────────────────────────────────────────────────────────────────────────

def _apply_inline_markup(text: str) -> str:
    """Transform **bold**, *italic*, `code`, and URLs to ANSI."""
    # Inline code first (don't process markup inside)
    text = re.sub(
        r"`([^`]+)`",
        lambda m: f"{C_VALUE}{m.group(1)}{RESET}",
        text,
    )
    # Bold
    text = re.sub(
        r"\*\*(.+?)\*\*",
        lambda m: f"{BOLD}{C_HIGHLIGHT}{m.group(1)}{RESET}",
        text,
    )
    # Italic
    text = re.sub(
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)",
        lambda m: f"{ITALIC}{C_TEXT}{m.group(1)}{RESET}",
        text,
    )
    # URL highlighting
    text = re.sub(
        r"(https?://\S+)",
        lambda m: f"{UNDERLINE}{C_ACCENT}{m.group(1)}{RESET}",
        text,
    )
    return text


def _render_usage_bar(used: int, total: int) -> str:
    """Render a compact context usage progress bar."""
    bar_w = 20
    pct = min(1.0, used / max(1, total))
    filled = int(pct * bar_w)
    empty = bar_w - filled
    # Color shifts: green → yellow → red
    if pct < 0.6:
        color = C_SUCCESS
    elif pct < 0.85:
        color = C_WARNING
    else:
        color = C_ERROR
    bar = f"{color}{'█' * filled}{C_BORDER}{'░' * empty}{RESET}"
    pct_str = f"{pct*100:.0f}%"
    return f"{bar} {color}{pct_str}{RESET} {C_DIM}{used:,}/{total:,}{RESET}"


_MAX_VISIBLE_TOOLS = 3  # Show at most N tool calls before collapsing
_MAX_VISIBLE_WARNINGS = 2


def _render_prompt_and_response(result: ChatResult, state: TUIState | None = None) -> None:
    w = _get_term_width()

    # Store tool events on state for /tools command
    if state is not None:
        state.last_tool_events = list(result.tool_events)
        state.last_warnings = list(result.warnings)

    # Compact metadata line — attachments + tools + warnings on minimal lines
    meta_parts: list[str] = []
    if result.attachments:
        att_list = ", ".join(result.attachments[:3])
        meta_parts.append(f"{C_ACCENT}📎 {att_list}{RESET}")
    if result.tool_events:
        meta_parts.append(f"{C_LABEL}{_SYM_BULLET} {len(result.tool_events)} tools{RESET}")
    if result.warnings:
        meta_parts.append(f"{C_WARNING}{_SYM_WARN} {len(result.warnings)} warnings{RESET}")
    if meta_parts:
        print(f"  {C_DIM}│{RESET} {'  '.join(meta_parts)}")

    # Tool events — collapsible tree (show first N, collapse rest)
    if result.tool_events:
        visible = result.tool_events[:_MAX_VISIBLE_TOOLS]
        hidden_count = len(result.tool_events) - _MAX_VISIBLE_TOOLS
        for i, event in enumerate(visible):
            is_last = (i == len(visible) - 1) and hidden_count <= 0
            connector = "└─" if is_last else "├─"
            print(f"  {C_DIM}│  {connector}{RESET} {C_VALUE}{event}{RESET}")
        if hidden_count > 0:
            print(f"  {C_DIM}│  └─ ... {C_ACCENT}{hidden_count} more{RESET} {C_DIM}(type {C_CMD}/tools{C_DIM} to expand){RESET}")

    # Warnings — collapsible
    if result.warnings:
        visible_w = result.warnings[:_MAX_VISIBLE_WARNINGS]
        hidden_w = len(result.warnings) - _MAX_VISIBLE_WARNINGS
        for warning in visible_w:
            print(f"  {C_DIM}│{RESET}  {C_WARNING}{_SYM_WARN} {warning}{RESET}")
        if hidden_w > 0:
            print(f"  {C_DIM}│{RESET}  {C_WARNING}{_SYM_WARN} ... {hidden_w} more warnings{RESET}")

    # Response header
    print()
    print(f"  {C_RESPONSE}◂ Assistant{RESET}")

    # Indent and wrap markdown-ish response text with inline markup.
    in_code = False
    code_lang = ""
    body_width = max(30, w - 6)
    for raw_line in result.text.splitlines():
        stripped = raw_line.rstrip("\n")
        if stripped.strip().startswith("```"):
            if not in_code:
                in_code = True
                code_lang = stripped.strip()[3:].strip()
                lang_label = f" {C_DIM}{code_lang}{RESET}" if code_lang else ""
                print(f"  {C_BORDER}┌{'─' * 40}{RESET}{lang_label}")
            else:
                in_code = False
                code_lang = ""
                print(f"  {C_BORDER}└{'─' * 40}{RESET}")
            continue
        if in_code:
            print(f"  {C_BORDER}│{RESET} {C_VALUE}{stripped}{RESET}")
            continue
        if not stripped.strip():
            print()
            continue
        text = stripped.strip()
        if text.startswith("#"):
            level = len(text) - len(text.lstrip("#"))
            heading = text.lstrip("#").strip()
            if level <= 1:
                for part in textwrap.wrap(heading, width=body_width):
                    print(f"  {C_BRAND_BOLD}{part}{RESET}")
            elif level == 2:
                for part in textwrap.wrap(heading, width=body_width):
                    print(f"  {BOLD}{C_ACCENT}{part}{RESET}")
            else:
                for part in textwrap.wrap(heading, width=body_width):
                    print(f"  {BOLD}{C_TEXT}{part}{RESET}")
            continue
        bullet_prefix = ""
        bullet_match = re.match(r"^(\d+\.\s+|[-*]\s+)(.+)$", text)
        if bullet_match:
            bullet_prefix = bullet_match.group(1).strip() + " "
            text = bullet_match.group(2).strip()
        # Apply inline markup
        text = _apply_inline_markup(text)
        wrapped = textwrap.wrap(
            _strip_ansi(text),
            width=body_width - len(bullet_prefix),
            break_long_words=False,
            replace_whitespace=False,
        )
        if not wrapped:
            print()
            continue
        # For wrapped text, re-apply markup to first line (best effort)
        for i, part in enumerate(wrapped):
            prefix = bullet_prefix if i == 0 else " " * len(bullet_prefix)
            display = _apply_inline_markup(part) if i == 0 else f"{C_TEXT}{part}{RESET}"
            print(f"  {C_TEXT}{prefix}{RESET}{display}")

    # Usage — compact bottom bar
    if result.usage_lines:
        print()
        # Try to render a context bar from usage lines
        ctx_match = None
        for uline in result.usage_lines:
            m = re.match(r"context_window=([\d,]+)/([\d,]+)", uline)
            if m:
                ctx_match = m
                break
        if ctx_match:
            used = int(ctx_match.group(1).replace(",", ""))
            total = int(ctx_match.group(2).replace(",", ""))
            print(f"  {C_DIM}ctx{RESET} {_render_usage_bar(used, total)}")
        else:
            compact_usage = "  ".join(result.usage_lines[:2])
            print(f"  {C_DIM}{compact_usage}{RESET}")
    print()


def _print_tool_events_expanded(state: TUIState) -> None:
    """Print all tool events from the last turn (used by /tools command)."""
    w = _get_term_width()
    rule_w = min(60, w - 4)
    rule_pad = max(0, (w - rule_w) // 2)
    thin_rule = f"{' ' * rule_pad}{C_BORDER}{_SYM_RULE * rule_w}{RESET}"
    print()
    print(f"  {C_BRAND_BOLD}Tool Events (Last Turn){RESET}")
    print(thin_rule)
    if not state.last_tool_events:
        print(f"  {C_DIM}No tool events from the last turn.{RESET}")
        print()
        return
    print(f"  {C_LABEL}{_SYM_BULLET} {len(state.last_tool_events)} tools{RESET}")
    for i, event in enumerate(state.last_tool_events):
        connector = "└─" if i == len(state.last_tool_events) - 1 else "├─"
        print(f"  {C_DIM}{connector}{RESET} {C_VALUE}{event}{RESET}")
    if state.last_warnings:
        print()
        print(f"  {C_WARNING}{_SYM_WARN} {len(state.last_warnings)} warnings{RESET}")
        for warning in state.last_warnings:
            print(f"    {C_WARNING}{warning}{RESET}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Prompt construction
# ─────────────────────────────────────────────────────────────────────────────

def _build_prompt_prefix(state: TUIState) -> str:
    """Build the styled prompt prefix — compact and clean."""
    backend_short = "cdx" if state.backend == "codex" else "oai"
    session_short = state.session_id.split("-")[-1][:6]
    cwd_name = state.cwd.name or str(state.cwd)
    return (
        f"{C_DIM}{cwd_name}{RESET}"
        f" {C_BRAND_BOLD}mtp{RESET}"
        f"{C_DIM}:{RESET}"
        f"{C_ACCENT_DIM}{backend_short}{RESET}"
        f"{C_DIM}:{RESET}"
        f"{C_VALUE}{session_short}{RESET}"
        f" {C_PROMPT_ARROW}{_SYM_PROMPT_ARROW}{RESET} "
    )


def _sanitize_paste_artifacts(raw: str) -> str:
    # Some terminals may include bracketed-paste control markers in input.
    return raw.replace("\x1b[200~", "").replace("\x1b[201~", "")


def _confirm_large_input(raw: str) -> bool:
    if len(raw) < 280:
        return True
    preview = _shorten_text(raw, 180)
    print(f"  {C_WARNING}Large pasted input detected.{RESET}")
    print(f"  {C_DIM}Preview:{RESET} {C_TEXT}{preview}{RESET}")
    decision = input(f"  {C_DIM}Press Enter to send, or type 'cancel' to abort:{RESET} ").strip().lower()
    return decision not in {"cancel", "c", "no", "n"}


def _compose_multiline_prompt() -> str | None:
    """Multi-line compose mode with box UI."""
    w = _get_term_width()
    
    # Draw compose box header
    print()
    print(_input_box_top(width=w, label="compose mode"))
    print(f"{C_BORDER}{_SYM_V}{RESET} {C_DIM}Type multiple lines. Use /send to submit or /cancel to abort.{RESET}")
    print(_box_separator(width=w))
    
    lines: list[str] = []
    while True:
        try:
            line = input(f"{C_BORDER}{_SYM_V}{RESET} {C_ACCENT_DIM}...{RESET} ")
        except (EOFError, KeyboardInterrupt):
            print(_input_box_bottom(width=w))
            return None
        marker = line.strip().lower()
        if marker == "/cancel":
            print(_input_box_bottom(width=w))
            return None
        if marker == "/send":
            break
        lines.append(line)
    
    print(_input_box_bottom(width=w))
    composed = "\n".join(lines).strip()
    return composed or None


# ─────────────────────────────────────────────────────────────────────────────
# Interrupt Handler
# ─────────────────────────────────────────────────────────────────────────────

_interrupt_requested = threading.Event()


# ─────────────────────────────────────────────────────────────────────────────
# Main Loop
# ─────────────────────────────────────────────────────────────────────────────

def run_tui(args) -> int:
    session_store = JsonSessionStore(db_path=args.session_db)
    initial_session_id = args.session_id or _new_session_id()
    state = TUIState(
        backend=args.backend,
        codex_model=args.codex_model,
        openai_model=args.openai_model,
        max_rounds=int(args.max_rounds),
        cwd=Path(args.cwd).expanduser().resolve(),
        autoresearch=bool(args.autoresearch),
        research_instructions=args.research_instructions,
        reasoning_effort=str(getattr(args, "reasoning_effort", "medium")),
        codex_sandbox_mode="workspace-write",  # Default to workspace-write mode for convenience
        last_usage_lines=[],
        transcript=[],
        session_store=session_store,
        session_id=initial_session_id,
        session_label=None,
        user_id="tui-user",
        codex_session_id=None,
    )
    if args.session_id:
        existing = _load_session_record(state, args.session_id)
        if existing is not None:
            _load_session_into_state(state, existing)
    _save_tui_session(state)
    _animate_boot(state)

    # ── Build prompt session (prompt_toolkit or fallback) ────────────────
    ptk_session = _build_prompt_session(state, lambda: _print_banner(state))


    while True:
        # Track if we need to close the box on exception
        box_opened = False
        bottom_border = None
        
        try:
            if ptk_session is not None:
                # ── prompt_toolkit input with bottom toolbar & completions ──
                # Draw input box frame
                top_border, prompt_html, bottom_border = _build_prompt_prefix_html_with_box(state)
                print(top_border)
                box_opened = True
                
                toolbar = lambda: HTML(_build_bottom_toolbar(state))
                raw = ptk_session.prompt(
                    HTML(prompt_html),
                    bottom_toolbar=toolbar,
                )
                
                # Close the box after input
                print(bottom_border)
                box_opened = False
            else:
                # ── Fallback: plain input() ──
                # Draw input box frame for fallback mode too
                w = _get_term_width()
                # Use 3-letter abbreviation for backend
                if state.backend == "codex":
                    backend_short = "cdx"
                else:
                    # For MTP providers, use first 3 letters or full name if shorter
                    backend_short = state.backend[:3] if len(state.backend) > 3 else state.backend
                session_short = state.session_id.split("-")[-1][:6]
                label = f"mtp:{backend_short}:{session_short}"
                
                top_border = _input_box_top(width=w, label=label)
                bottom_border = _input_box_bottom(width=w)
                
                print(top_border)
                box_opened = True
                prompt_prefix = f"{C_BORDER}{_SYM_V}{RESET} {C_PROMPT_ARROW}{_SYM_PROMPT_ARROW}{RESET} "
                raw = input(prompt_prefix)
                print(bottom_border)
                box_opened = False
        except KeyboardInterrupt:
            # Close box if it was opened
            if box_opened and bottom_border:
                print(bottom_border)
            # Ctrl+C at prompt → stay in TUI
            print(f"\n{C_DIM}Interrupted. Type /exit or press Ctrl+D to quit.{RESET}")
            continue
        except EOFError:
            # Close box if it was opened
            if box_opened and bottom_border:
                print(bottom_border)
            # Ctrl+D → exit
            print(f"\n{C_BRAND}Goodbye!{RESET}\n")
            return 0
        raw = _sanitize_paste_artifacts(raw).strip()
        if not raw:
            continue
        if raw.lower() in {"/compose", "compose"}:
            composed = _compose_multiline_prompt()
            if not composed:
                print(f"  {C_WARNING}Compose cancelled.{RESET}")
                continue
            raw = composed
        if not raw.startswith("/") and not _confirm_large_input(raw):
            print(f"  {C_WARNING}Input cancelled.{RESET}")
            continue
        raw = _normalize_input(raw)
        if raw.startswith("/"):
            out = _handle_command(state, raw)
            if out == "__compose__":
                composed = _compose_multiline_prompt()
                if not composed:
                    print(f"  {C_WARNING}Compose cancelled.{RESET}")
                    continue
                raw = composed
                # Continue as regular prompt below.
            else:
                if out == "__exit__":
                    print(f"\n{C_BRAND}Goodbye!{RESET}\n")
                    return 0
                if out:
                    print(f"  {out}")
                    # Show toast for model/setting changes
                    stripped_out = _strip_ansi(out)
                    if stripped_out.startswith(_SYM_OK) and len(stripped_out) < 80:
                        _toast(stripped_out, kind="success", duration=2.0)
                continue

        expanded_prompt, attachments, attachment_warnings = _collect_prompt_attachments(raw, state.cwd)
        active_model = _active_model_name(state)

        # Build status line
        if state.backend == "codex":
            # Codex shows reasoning
            status_line = (
                f"  {C_DIM}> {state.backend} {_SYM_DOT} {active_model} "
                f"{_SYM_DOT} reasoning={state.reasoning_effort}{RESET}"
            )
        else:
            # MTP providers don't show reasoning
            status_line = f"  {C_DIM}> {state.backend} {_SYM_DOT} {active_model}{RESET}"

        print(status_line)
        spinner_preset = "reasoning" if state.reasoning_effort in ("high", "xhigh") else "default"
        spinner = _Spinner(label="Thinking", preset=spinner_preset)
        spinner.start()
        _interrupt_requested.clear()
        try:
            if state.backend == "codex":
                # Codex backend (external binary)
                result = _run_codex_prompt(state, expanded_prompt)
                result.attachments = attachments
                result.warnings = [*attachment_warnings, *result.warnings]
            else:
                # MTP Provider backend (SDK)
                result = _run_mtp_prompt(state, expanded_prompt)
                result.attachments = attachments
                result.warnings = [*attachment_warnings, *result.warnings]
        except KeyboardInterrupt:
            spinner.stop()
            print(f"\n  {C_WARNING}⚡ Request interrupted.{RESET}")
            _toast("Request interrupted", kind="warning", duration=2.0)
            continue
        except Exception as exc:  # noqa: BLE001
            spinner.stop()
            print(f"  {C_ERROR}{_SYM_ERR} Error:{RESET} {exc}")
            continue
        spinner.stop()
        _record_turn(state, raw, result)
        _render_prompt_and_response(result, state=state)
