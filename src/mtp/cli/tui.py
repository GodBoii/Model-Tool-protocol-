from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from typing import Any
from uuid import uuid4

from mtp import Agent, JsonSessionStore, SessionRecord
from mtp.providers import OpenAI
from mtp.toolkits.local import register_local_toolkits


_BACKENDS = {"codex", "mtp-openai"}
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


# ─────────────────────────────────────────────────────────────────────────────
# ANSI / Styling Helpers (zero-dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _setup_console() -> None:
    """Configure the console for UTF-8 and ANSI support on Windows."""
    if sys.platform == "win32":
        # Set console output code page to UTF-8
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # SetConsoleOutputCP(65001)  — UTF-8
            kernel32.SetConsoleOutputCP(65001)
            # STD_OUTPUT_HANDLE = -11
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass
        # Reconfigure stdout to UTF-8
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_setup_console()


def _supports_unicode() -> bool:
    """Detect whether the terminal can render Unicode box-drawing characters."""
    try:
        encoding = (sys.stdout.encoding or "").lower()
        return encoding in {"utf-8", "utf8", "utf_8"}
    except Exception:
        return False


_UNICODE_ENABLED = _supports_unicode()


def _supports_color() -> bool:
    """Detect whether the terminal supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if sys.platform == "win32":
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_COLOR_ENABLED = _supports_color()


def _sgr(code: str) -> str:
    """Return an ANSI SGR escape if color is enabled, else empty string."""
    return f"\033[{code}m" if _COLOR_ENABLED else ""


# ── Core colors ──────────────────────────────────────────────────────────────

RESET       = _sgr("0")
BOLD        = _sgr("1")
DIM         = _sgr("2")
ITALIC      = _sgr("3")
UNDERLINE   = _sgr("4")

# 256-color / truecolor helpers
def _fg256(n: int) -> str:
    return _sgr(f"38;5;{n}")

def _bg256(n: int) -> str:
    return _sgr(f"48;5;{n}")

def _fg_rgb(r: int, g: int, b: int) -> str:
    return _sgr(f"38;2;{r};{g};{b}")

def _bg_rgb(r: int, g: int, b: int) -> str:
    return _sgr(f"48;2;{r};{g};{b}")


# ── Semantic palette ─────────────────────────────────────────────────────────
# A rich purple/violet → cyan gradient feel, modern dark-terminal aesthetic.

C_BRAND         = _fg_rgb(167, 139, 250)   # Soft violet  — primary brand
C_BRAND_BOLD    = BOLD + _fg_rgb(167, 139, 250)
C_ACCENT        = _fg_rgb(99, 220, 255)    # Electric cyan — accent
C_ACCENT_DIM    = _fg_rgb(70, 160, 190)    # Muted cyan
C_SUCCESS       = _fg_rgb(52, 211, 153)    # Mint green   — success/active
C_WARNING       = _fg_rgb(251, 191, 36)    # Amber        — warnings
C_ERROR         = _fg_rgb(248, 113, 113)   # Soft red     — errors
C_DIM           = _fg_rgb(100, 100, 120)   # Muted grey   — secondary text
C_TEXT          = _fg_rgb(220, 220, 230)    # Off-white    — body text
C_LABEL         = _fg_rgb(180, 170, 220)   # Lavender     — labels
C_HIGHLIGHT     = BOLD + _fg_rgb(255, 255, 255)  # Bright white
C_MODEL         = _fg_rgb(250, 204, 21)    # Gold         — model names
C_CMD           = _fg_rgb(129, 140, 248)   # Indigo       — commands
C_KEY           = _fg_rgb(192, 132, 252)   # Purple       — keyboard shortcuts
C_VALUE         = _fg_rgb(110, 231, 183)   # Seafoam      — values
C_BORDER        = _fg_rgb(75, 75, 100)     # Dark border
C_PROMPT_ARROW  = _fg_rgb(167, 139, 250)   # Brand violet for prompt
C_RESPONSE      = _fg_rgb(196, 181, 253)   # Light violet for assistant label


# ── Drawing primitives ───────────────────────────────────────────────────────

def _get_term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except (ValueError, OSError):
        return 80


def _hrule(char: str = "─", color: str = C_BORDER) -> str:
    w = _get_term_width()
    return f"{color}{char * w}{RESET}"


def _centered(text: str, width: int | None = None, pad_char: str = " ") -> str:
    """Center raw text (strips ANSI for width calc)."""
    w = width or _get_term_width()
    visible = _strip_ansi(text)
    padding = max(0, (w - len(visible)) // 2)
    return pad_char * padding + text


def _strip_ansi(s: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", s)


def _box_line(content: str, width: int | None = None) -> str:
    """Render a line inside a box with │ borders."""
    w = (width or _get_term_width()) - 4  # account for "│ " + " │"
    visible_len = len(_strip_ansi(content))
    pad = max(0, w - visible_len)
    return f"{C_BORDER}│{RESET} {content}{' ' * pad} {C_BORDER}│{RESET}"


def _box_top(width: int | None = None) -> str:
    w = (width or _get_term_width()) - 2
    return f"{C_BORDER}╭{'─' * w}╮{RESET}"


def _box_bottom(width: int | None = None) -> str:
    w = (width or _get_term_width()) - 2
    return f"{C_BORDER}╰{'─' * w}╯{RESET}"


def _box_separator(width: int | None = None) -> str:
    w = (width or _get_term_width()) - 2
    return f"{C_BORDER}├{'─' * w}┤{RESET}"


# ── ASCII Art Logo ───────────────────────────────────────────────────────────

_LOGO_LINES = [
    r"  ███╗   ███╗ ████████╗ ██████╗  ",
    r"  ████╗ ████║ ╚══██╔══╝ ██╔══██╗ ",
    r"  ██╔████╔██║    ██║    ██████╔╝ ",
    r"  ██║╚██╔╝██║    ██║    ██╔═══╝  ",
    r"  ██║ ╚═╝ ██║    ██║    ██║      ",
    r"  ╚═╝     ╚═╝    ╚═╝    ╚═╝      ",
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
    last_usage_lines: list[str]
    transcript: list[TranscriptTurn]
    session_store: JsonSessionStore
    session_id: str
    session_label: str | None
    user_id: str | None
    agent: Agent.MTPAgent | None = None
    codex_bin: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Banner / Welcome Screen
# ─────────────────────────────────────────────────────────────────────────────

def _print_banner(state: TUIState) -> None:
    from mtp import __version__
    active_model = state.codex_model if state.backend == "codex" else state.openai_model
    w = _get_term_width()

    print()
    # Top border
    print(_box_top(w))

    # Empty padding line
    print(_box_line("", w))

    # Render logo
    for logo_line in _render_logo():
        # Re-center inside box
        visible = _strip_ansi(logo_line).strip()
        inner_w = w - 4
        pad = max(0, (inner_w - len(visible)) // 2)
        # Rebuild with color
        idx = _render_logo().index(logo_line) if False else 0
        print(_box_line(" " * pad + logo_line.strip(), w))

    # Tagline
    print(_box_line("", w))
    tagline = f"{C_DIM}Model Tool Protocol{RESET}  {C_BRAND_BOLD}v{__version__}{RESET}"
    tagline_vis = _strip_ansi(tagline)
    tag_pad = max(0, (w - 4 - len(tagline_vis)) // 2)
    print(_box_line(" " * tag_pad + tagline, w))
    subtitle = f"{C_DIM}Interactive terminal UI · SDK + Codex CLI bridge{RESET}"
    sub_vis = _strip_ansi(subtitle)
    sub_pad = max(0, (w - 4 - len(sub_vis)) // 2)
    print(_box_line(" " * sub_pad + subtitle, w))
    print(_box_line("", w))

    # Separator
    print(_box_separator(w))
    print(_box_line("", w))

    # Models section
    model_header = f"  {C_LABEL}{'Models'}{RESET}"
    print(_box_line(model_header, w))
    for idx, (model, desc) in enumerate(_MODEL_PRESETS, start=1):
        selected = model in {state.codex_model, state.openai_model}
        marker = f" {C_SUCCESS}●{RESET}" if selected else f" {C_DIM}○{RESET}"
        shortcut = f"{C_KEY}[{idx}]{RESET}"
        model_name = f"{C_MODEL}{model}{RESET}"
        description = f"{C_DIM}{desc}{RESET}"
        print(_box_line(f"  {marker} {shortcut} {model_name}  {description}", w))
    print(_box_line("", w))

    # Reasoning section
    reasoning_header = f"  {C_LABEL}{'Reasoning'}{RESET}"
    print(_box_line(reasoning_header, w))
    reasoning_items = []
    for num, name in _REASONING_SHORTCUTS.items():
        if name == state.reasoning_effort:
            reasoning_items.append(f"{C_KEY}[{num}]{RESET} {C_SUCCESS}{name}{RESET}")
        else:
            reasoning_items.append(f"{C_KEY}[{num}]{RESET} {C_DIM}{name}{RESET}")
    print(_box_line(f"    {'  '.join(reasoning_items)}", w))
    print(_box_line("", w))

    # Separator
    print(_box_separator(w))
    print(_box_line("", w))

    # Quick Commands section
    cmd_header = f"  {C_LABEL}{'Commands'}{RESET}"
    print(_box_line(cmd_header, w))
    cmds = [
        (f"{C_CMD}/help{RESET}", "Show full command reference"),
        (f"{C_CMD}/compose{RESET}", "Multi-line prompt mode"),
        (f"{C_CMD}/new{RESET}", "Start a new chat"),
        (f"{C_CMD}/history{RESET}", "Show recent turns"),
        (f"{C_CMD}/model{RESET}", "Switch model"),
        (f"{C_CMD}/backend{RESET}", "Switch backend"),
        (f"{C_CMD}/sessions{RESET}", "List saved chats"),
        (f"{C_CMD}/status{RESET}", "Show session info"),
        (f"{C_CMD}/exit{RESET}", "Exit TUI"),
    ]
    # Render in 2 columns
    half = (len(cmds) + 1) // 2
    for i in range(half):
        left = cmds[i]
        right = cmds[i + half] if i + half < len(cmds) else ("", "")
        left_str = f"    {left[0]}  {C_DIM}{left[1]}{RESET}"
        right_str = f"    {right[0]}  {C_DIM}{right[1]}{RESET}" if right[0] else ""
        # We need careful padding for columns
        left_vis = len(_strip_ansi(left_str))
        col_w = (w - 4) // 2
        pad = max(0, col_w - left_vis)
        print(_box_line(f"{left_str}{' ' * pad}{right_str}", w))
    print(_box_line("", w))

    # Separator
    print(_box_separator(w))
    print(_box_line("", w))

    # Current session info
    session_header = f"  {C_LABEL}{'Session'}{RESET}"
    print(_box_line(session_header, w))
    backend_val = f"{C_SUCCESS}{state.backend}{RESET}" if state.backend == "codex" else f"{C_ACCENT}{state.backend}{RESET}"
    model_val = f"{C_MODEL}{active_model or '(codex-default)'}{RESET}"
    reasoning_val = f"{C_VALUE}{state.reasoning_effort}{RESET}"
    cwd_val = f"{C_TEXT}{state.cwd}{RESET}"
    session_val = f"{C_VALUE}{state.session_id}{RESET}"
    turns_val = f"{C_VALUE}{len(state.transcript)}{RESET}"

    print(_box_line(f"    {C_DIM}session{RESET}     {session_val}", w))
    print(_box_line(f"    {C_DIM}turns{RESET}       {turns_val}", w))
    print(_box_line(f"    {C_DIM}backend{RESET}     {backend_val}", w))
    print(_box_line(f"    {C_DIM}model{RESET}       {model_val}", w))
    print(_box_line(f"    {C_DIM}reasoning{RESET}   {reasoning_val}", w))
    print(_box_line(f"    {C_DIM}cwd{RESET}         {cwd_val}", w))
    print(_box_line("", w))

    # Tips
    print(_box_separator(w))
    print(_box_line("", w))
    tip_icon = f"{C_WARNING}💡{RESET}"
    print(_box_line(f"  {tip_icon} {C_DIM}Include{RESET} {C_ACCENT}@relative/path.py{RESET} {C_DIM}in your prompt to attach file contents.{RESET}", w))
    print(_box_line(f"  {tip_icon} {C_DIM}Leading{RESET} {C_CMD}/{RESET} {C_DIM}is optional for commands (e.g.{RESET} {C_CMD}codex-login{RESET} {C_DIM}works too).{RESET}", w))
    print(_box_line("", w))

    # Bottom border
    print(_box_bottom(w))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Help Display
# ─────────────────────────────────────────────────────────────────────────────

def _print_help() -> None:
    w = _get_term_width()
    print()
    print(_box_top(w))
    print(_box_line(f"  {C_BRAND_BOLD}Command Reference{RESET}", w))
    print(_box_separator(w))

    sections = [
        ("Navigation", [
            ("/help", "Show this command reference"),
            ("/exit", "Exit TUI"),
            ("/compose", "Open multi-line compose mode"),
            ("/status", "Show current session state"),
            ("/new [label]", "Start a fresh chat session"),
            ("/load <session_id>", "Load a saved chat session"),
            ("/sessions", "List saved chat sessions"),
            ("/history [n]", "Show recent turns in this chat"),
            ("/clear", "Clear the terminal and redraw the banner"),
            ("/cd <dir>", "Change working directory"),
        ]),
        ("Backend & Model", [
            ("/backend codex|mtp-openai", "Switch active backend"),
            ("/models", "Show model + reasoning presets"),
            ("/model <name|1..4|default>", "Set model for active backend"),
            ("/reasoning <none|low|...|xhigh>", "Set reasoning effort (codex)"),
            ("/rounds <n>", "Set max_rounds (mtp-openai)"),
        ]),
        ("Research & Auth", [
            ("/autoresearch on|off", "Toggle autoresearch (mtp-openai)"),
            ("/research <text>", "Set research instructions"),
            ("/codex-login", "Run official codex login flow"),
        ]),
    ]

    for section_name, commands in sections:
        print(_box_line("", w))
        print(_box_line(f"  {C_LABEL}{section_name}{RESET}", w))
        for cmd, desc in commands:
            cmd_str = f"{C_CMD}{cmd}{RESET}"
            cmd_vis = len(_strip_ansi(cmd_str))
            pad = max(1, 36 - cmd_vis)
            print(_box_line(f"    {cmd_str}{' ' * pad}{C_DIM}{desc}{RESET}", w))

    print(_box_line("", w))
    print(_box_separator(w))
    print(_box_line("", w))
    print(_box_line(f"  {C_LABEL}Prompt UX{RESET}", w))
    print(_box_line(f"    {C_DIM}Type{RESET} {C_ACCENT}@path/to/file.py{RESET} {C_DIM}in a prompt to attach file context.{RESET}", w))
    print(_box_line(f"    {C_DIM}Example:{RESET} {C_TEXT}explain bug in @src/mtp/cli/tui.py and propose patch{RESET}", w))
    print(_box_line("", w))
    print(_box_bottom(w))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Model Matrix Display
# ─────────────────────────────────────────────────────────────────────────────

def _print_model_matrix(state: TUIState) -> None:
    w = _get_term_width()
    print()
    print(_box_top(w))
    print(_box_line(f"  {C_BRAND_BOLD}Model Presets{RESET}", w))
    print(_box_separator(w))
    print(_box_line("", w))

    for idx, (model, note) in enumerate(_MODEL_PRESETS, start=1):
        selected = model in {state.codex_model, state.openai_model}
        marker = f"{C_SUCCESS}●{RESET}" if selected else f"{C_DIM}○{RESET}"
        shortcut = f"{C_KEY}[{idx}]{RESET}"
        model_name = f"{C_MODEL}{model}{RESET}"
        desc = f"{C_DIM}{note}{RESET}"
        sel_tag = f"  {C_SUCCESS}← active{RESET}" if selected else ""
        print(_box_line(f"    {marker} {shortcut} {model_name}  {desc}{sel_tag}", w))

    print(_box_line("", w))
    print(_box_separator(w))
    print(_box_line("", w))

    print(_box_line(f"  {C_LABEL}Reasoning Levels{RESET}", w))
    reasoning_items = []
    for num, name in _REASONING_SHORTCUTS.items():
        if name == state.reasoning_effort:
            reasoning_items.append(f"{C_KEY}[{num}]{RESET} {C_SUCCESS}{name}{RESET}")
        else:
            reasoning_items.append(f"{C_KEY}[{num}]{RESET} {C_DIM}{name}{RESET}")
    print(_box_line(f"      {'   '.join(reasoning_items)}", w))

    print(_box_line("", w))
    print(_box_separator(w))
    print(_box_line("", w))
    print(_box_line(f"  {C_LABEL}Model-Specific Notes{RESET}", w))
    for model_key, notes in _REASONING_NOTES.items():
        print(_box_line(f"    {C_MODEL}{model_key}{RESET} {C_DIM}→{RESET} {C_VALUE}{notes}{RESET}", w))
    print(_box_line("", w))
    print(_box_line(f"  {C_DIM}Usage:{RESET} {C_CMD}/model 3{RESET} {C_DIM}and{RESET} {C_CMD}/reasoning high{RESET}  {C_DIM}or{RESET}  {C_CMD}/model gpt-5.4-mini{RESET}", w))
    print(_box_line("", w))
    print(_box_bottom(w))
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
    return state.openai_model


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
        "reasoning_effort": state.reasoning_effort,
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
        tool_name, tool_reasoning = _extract_codex_tool_signal(event, event_type)
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
            if tool_reasoning:
                tool_events.append(f"{tool_name}: {tool_reasoning}")
            else:
                tool_events.append(tool_name)
            continue
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
    color = C_DIM
    label = "event"
    if kind == "tool":
        color = C_LABEL
        label = "tool"
    elif kind == "warn":
        color = C_WARNING
        label = "warn"
    elif kind == "status":
        color = C_ACCENT
        label = "status"
    elif kind == "step":
        color = C_VALUE
        label = "step"
    print(f"  {color}[{label}]{RESET} {C_TEXT}{message}{RESET}")


def _emit_codex_live_line(raw_line: str, emitted: dict[str, bool]) -> None:
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
    tool_name, tool_reasoning = _extract_codex_tool_signal(event, event_type)
    if event_type in {"response.output_text.delta", "response.output_text"}:
        if not emitted.get("assistant_started"):
            emitted["assistant_started"] = True
            _emit_live_event("status", "assistant is drafting the response")
        return
    if tool_name:
        if tool_reasoning:
            _emit_live_event("tool", f"{tool_name}: {_shorten_text(tool_reasoning, 180)}")
        else:
            _emit_live_event("tool", tool_name)
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
    def _get_str(value: Any) -> str | None:
        if isinstance(value, str):
            text = value.strip()
            return text or None
        return None

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

    def _from_item(item: dict[str, Any]) -> tuple[str | None, str | None]:
        item_type = _get_str(item.get("type")) or _get_str(item.get("kind")) or ""
        if item_type in {"message", "assistant_message", "reasoning", "analysis"}:
            return None, None
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
            return None, None
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
        return normalized_name, reasoning

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
        return name, reasoning

    item = event.get("item")
    if isinstance(item, dict):
        name, reasoning = _from_item(item)
        if name:
            return name, reasoning

    payload = event.get("payload")
    if isinstance(payload, dict):
        name, reasoning = _from_item(payload)
        if name:
            return name, reasoning

    return None, None


def _run_codex_prompt(state: TUIState, prompt: str) -> ChatResult:
    codex_bin = state.codex_bin or _detect_codex_bin()
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

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as tmp:
        output_path = Path(tmp.name)

    cmd = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "-C",
        str(state.cwd),
        "--json",
        "--output-last-message",
        str(output_path),
    ]
    if state.codex_model:
        cmd.extend(["-m", state.codex_model])
    if state.reasoning_effort in _REASONING_EFFORTS and state.reasoning_effort != "none":
        cmd.extend(["-c", f'reasoning_effort="{state.reasoning_effort}"'])
    cmd.append(prompt)
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
    emitted: dict[str, bool] = {}
    if proc.stdout is not None:
        for line in proc.stdout:
            stdout_lines.append(line)
            _emit_codex_live_line(line, emitted)
    return_code = proc.wait()
    stdout_text = "".join(stdout_lines)
    text = ""
    try:
        if output_path.exists():
            text = output_path.read_text(encoding="utf-8", errors="replace").strip()
    finally:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass

    parsed_text, tool_events, parse_warnings, usage_lines = _parse_codex_json_events(stdout_text, state.codex_model)

    if return_code != 0:
        details = stdout_text.strip()
        hint = "Try: codex login"
        if details:
            return ChatResult(
                text=f"Codex exec failed (exit {return_code}).\n{details}\n{hint}",
                tool_events=tool_events,
                attachments=[],
                warnings=parse_warnings,
                usage_lines=usage_lines,
            )
        return ChatResult(
            text=f"Codex exec failed (exit {return_code}).\n{hint}",
            tool_events=tool_events,
            attachments=[],
            warnings=parse_warnings,
            usage_lines=usage_lines,
        )

    if text:
        final_text = text
    elif parsed_text:
        final_text = parsed_text
    elif stdout_text.strip():
        final_text = stdout_text.strip()
    else:
        final_text = "(Codex returned no final text.)"
    return ChatResult(
        text=final_text,
        tool_events=tool_events,
        attachments=[],
        warnings=parse_warnings,
        usage_lines=usage_lines,
    )


def _run_codex_login(state: TUIState) -> str:
    codex_bin = state.codex_bin or _detect_codex_bin()
    state.codex_bin = codex_bin
    if not codex_bin:
        return "Codex CLI not found on PATH. Install: npm install -g @openai/codex"
    proc = subprocess.run([codex_bin, "login"], text=True)
    if proc.returncode == 0:
        return f"{C_SUCCESS}✓ Codex login completed.{RESET}"
    return f"{C_ERROR}✗ Codex login exited with code {proc.returncode}.{RESET}"


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
    print()
    print(_box_top(w))
    print(_box_line(f"  {C_BRAND_BOLD}Session Status{RESET}", w))
    print(_box_separator(w))
    print(_box_line("", w))

    fields = [
        ("session_id", state.session_id, C_VALUE),
        ("session_label", state.session_label or "(none)", C_DIM),
        ("turns", str(len(state.transcript)), C_VALUE),
        ("backend", state.backend, C_SUCCESS),
        ("cwd", str(state.cwd), C_TEXT),
        ("codex_model", state.codex_model or "(codex-default)", C_MODEL),
        ("openai_model", state.openai_model, C_MODEL),
        ("max_rounds", str(state.max_rounds), C_VALUE),
        ("autoresearch", str(state.autoresearch), C_VALUE),
        ("research", state.research_instructions or "(none)", C_DIM),
        ("reasoning", state.reasoning_effort, C_VALUE),
    ]
    for label, value, color in fields:
        print(_box_line(f"    {C_LABEL}{label:<20}{RESET} {color}{value}{RESET}", w))
    usage_lines = state.last_usage_lines or ["(none)"]
    print(_box_line("", w))
    print(_box_line(f"    {C_LABEL}{'last_usage':<20}{RESET} {C_VALUE}{usage_lines[0]}{RESET}", w))
    for extra in usage_lines[1:]:
        print(_box_line(f"    {C_LABEL}{'':<20}{RESET} {C_VALUE}{extra}{RESET}", w))

    print(_box_line("", w))
    print(_box_bottom(w))
    print()


def _print_history(state: TUIState, limit: int | None = None) -> None:
    w = _get_term_width()
    turns = state.transcript[-limit:] if limit is not None else state.transcript
    print()
    print(_box_top(w))
    print(_box_line(f"  {C_BRAND_BOLD}Chat History{RESET}", w))
    print(_box_separator(w))
    if not turns:
        print(_box_line("", w))
        print(_box_line(f"  {C_DIM}No turns yet in this chat.{RESET}", w))
        print(_box_line("", w))
        print(_box_bottom(w))
        print()
        return
    for idx, turn in enumerate(turns, start=max(1, len(state.transcript) - len(turns) + 1)):
        print(_box_line("", w))
        meta = f"  {C_LABEL}Turn {idx}{RESET}  {C_DIM}{turn.created_at} · {turn.backend} · {turn.model}{RESET}"
        print(_box_line(meta, w))
        prompt_preview = turn.prompt.replace("\n", " ")
        response_preview = turn.response.replace("\n", " ")
        if len(prompt_preview) > 110:
            prompt_preview = prompt_preview[:107] + "..."
        if len(response_preview) > 110:
            response_preview = response_preview[:107] + "..."
        print(_box_line(f"    {C_ACCENT}User:{RESET} {C_TEXT}{prompt_preview}{RESET}", w))
        print(_box_line(f"    {C_SUCCESS}Assistant:{RESET} {C_TEXT}{response_preview}{RESET}", w))
    print(_box_line("", w))
    print(_box_bottom(w))
    print()


def _print_saved_sessions(state: TUIState) -> None:
    sessions = _list_saved_sessions(state.session_store)
    w = _get_term_width()
    print()
    print(_box_top(w))
    print(_box_line(f"  {C_BRAND_BOLD}Saved Sessions{RESET}", w))
    print(_box_separator(w))
    if not sessions:
        print(_box_line("", w))
        print(_box_line(f"  {C_DIM}No saved sessions yet.{RESET}", w))
        print(_box_line("", w))
        print(_box_bottom(w))
        print()
        return
    for record in sessions[:12]:
        tui_meta = record.metadata.get("tui") if isinstance(record.metadata, dict) else {}
        tui_meta = tui_meta if isinstance(tui_meta, dict) else {}
        label = str(tui_meta.get("session_label") or "(unnamed)")
        turn_count = int(tui_meta.get("turn_count") or 0)
        backend = str(tui_meta.get("backend") or "unknown")
        updated = str(tui_meta.get("updated_at") or record.updated_at)
        active = f" {C_SUCCESS}← active{RESET}" if record.session_id == state.session_id else ""
        print(_box_line("", w))
        print(_box_line(f"  {C_VALUE}{record.session_id}{RESET}{active}", w))
        print(_box_line(f"    {C_TEXT}{label}{RESET}", w))
        print(_box_line(f"    {C_DIM}{backend} · turns={turn_count} · updated={updated}{RESET}", w))
    print(_box_line("", w))
    print(_box_bottom(w))
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
    if cmd in {"/new", "/reset"}:
        _reset_chat(state, new_session=True, session_label=arg or None)
        label_text = f" ({arg})" if arg else ""
        return f"{C_SUCCESS}✓{RESET} Started new chat {C_VALUE}{state.session_id}{RESET}{label_text}."
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
        record = _load_session_record(state, arg)
        if record is None:
            return f"{C_ERROR}✗ Session not found:{RESET} {arg}"
        _load_session_into_state(state, record)
        return (
            f"{C_SUCCESS}✓{RESET} Loaded session {C_VALUE}{state.session_id}{RESET} "
            f"with {C_VALUE}{len(state.transcript)}{RESET} turns."
        )
    if cmd == "/models":
        _print_model_matrix(state)
        return None
    if cmd == "/backend":
        if arg not in _BACKENDS:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/backend codex|mtp-openai{RESET}"
        state.backend = arg
        _save_tui_session(state)
        return f"{C_SUCCESS}✓{RESET} Switched backend to {C_VALUE}{arg}{RESET}."
    if cmd == "/model":
        if not arg:
            _print_model_matrix(state)
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/model <name|1..4|default>{RESET}"
        resolved = _resolve_model(arg)
        if state.backend == "codex":
            if resolved.lower() in {"default", "auto"}:
                state.codex_model = None
                _save_tui_session(state)
                return f"{C_SUCCESS}✓{RESET} Codex model reset to CLI default."
            state.codex_model = resolved
            _save_tui_session(state)
            return f"{C_SUCCESS}✓{RESET} Codex model set to {C_MODEL}{resolved}{RESET}."
        state.openai_model = resolved
        state.agent = None
        _save_tui_session(state)
        return f"{C_SUCCESS}✓{RESET} OpenAI model set to {C_MODEL}{resolved}{RESET}. Agent reloaded."
    if cmd == "/reasoning":
        if not arg:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/reasoning <none|low|medium|high|xhigh>{RESET}"
        resolved_reasoning = _resolve_reasoning(arg)
        if resolved_reasoning is None:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/reasoning <none|low|medium|high|xhigh>{RESET}"
        state.reasoning_effort = resolved_reasoning
        _save_tui_session(state)
        return (
            f"{C_SUCCESS}✓{RESET} reasoning_effort set to {C_VALUE}{resolved_reasoning}{RESET}. "
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
        return f"{C_SUCCESS}✓{RESET} max_rounds set to {C_VALUE}{rounds}{RESET}."
    if cmd == "/autoresearch":
        lowered = arg.lower()
        if lowered not in {"on", "off"}:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/autoresearch on|off{RESET}"
        state.autoresearch = lowered == "on"
        state.agent = None
        _save_tui_session(state)
        return f"{C_SUCCESS}✓{RESET} autoresearch={C_VALUE}{state.autoresearch}{RESET}. Agent reloaded."
    if cmd == "/research":
        state.research_instructions = arg or None
        state.agent = None
        _save_tui_session(state)
        return f"{C_SUCCESS}✓{RESET} research_instructions updated. Agent reloaded."
    if cmd == "/codex-login":
        return _run_codex_login(state)
    if cmd == "/cd":
        if not arg:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/cd <dir>{RESET}"
        target = Path(arg).expanduser().resolve()
        if not target.exists() or not target.is_dir():
            return f"{C_ERROR}✗ Directory not found:{RESET} {target}"
        state.cwd = target
        state.agent = None
        _save_tui_session(state)
        return f"{C_SUCCESS}✓{RESET} cwd set to {C_TEXT}{target}{RESET}. Agent reloaded."
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
        "models",
        "model",
        "reasoning",
        "rounds",
        "codex-login",
        "autoresearch",
        "research",
        "cd",
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

class _Spinner:
    """An animated terminal spinner shown while waiting for a response."""

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, label: str = "Thinking"):
        self._label = label
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
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
            frame = self._FRAMES[idx % len(self._FRAMES)]
            color = spin_colors[idx % len(spin_colors)]
            elapsed = ""
            sys.stdout.write(f"\r  {color}{frame}{RESET} {C_DIM}{self._label}...{RESET}{elapsed}")
            sys.stdout.flush()
            idx += 1
            time.sleep(0.08)


# ─────────────────────────────────────────────────────────────────────────────
# Response Rendering
# ─────────────────────────────────────────────────────────────────────────────

def _render_prompt_and_response(result: ChatResult) -> None:
    w = _get_term_width()

    if result.attachments:
        print(f"\n  {C_ACCENT}📎 Attachments{RESET}")
        for item in result.attachments:
            print(f"     {C_DIM}├─{RESET} {C_TEXT}{item}{RESET}")

    if result.tool_events:
        print(f"\n  {C_LABEL}🔧 Tools{RESET}")
        for i, event in enumerate(result.tool_events):
            connector = "└─" if i == len(result.tool_events) - 1 else "├─"
            print(f"     {C_DIM}{connector}{RESET} {C_VALUE}{event}{RESET}")

    if result.warnings:
        print(f"\n  {C_WARNING}⚠  Notes{RESET}")
        for i, warning in enumerate(result.warnings):
            connector = "└─" if i == len(result.warnings) - 1 else "├─"
            print(f"     {C_DIM}{connector}{RESET} {C_WARNING}{warning}{RESET}")

    if result.usage_lines:
        print(f"\n  {C_LABEL}📊 Usage{RESET}")
        for i, line in enumerate(result.usage_lines):
            connector = "└─" if i == len(result.usage_lines) - 1 else "├─"
            print(f"     {C_DIM}{connector}{RESET} {C_VALUE}{line}{RESET}")

    # Response
    print()
    print(f"  {C_RESPONSE}{'─' * 3} Assistant {C_BORDER}{'─' * (w - 18)}{RESET}")
    print()

    # Indent and wrap markdown-ish response text for terminal readability.
    in_code = False
    body_width = max(30, w - 6)
    for raw_line in result.text.splitlines():
        stripped = raw_line.rstrip("\n")
        if stripped.strip().startswith("```"):
            in_code = not in_code
            fence = stripped.strip() or "```"
            print(f"  {C_VALUE}{fence}{RESET}")
            continue
        if in_code:
            print(f"  {C_VALUE}{stripped}{RESET}")
            continue
        if not stripped.strip():
            print()
            continue
        text = stripped.strip()
        if text.startswith("#"):
            heading = text.lstrip("#").strip()
            for part in textwrap.wrap(heading, width=body_width):
                print(f"  {C_BRAND_BOLD}{part}{RESET}")
            continue
        bullet_prefix = ""
        bullet_match = re.match(r"^(\d+\.\s+|[-*]\s+)(.+)$", text)
        if bullet_match:
            bullet_prefix = bullet_match.group(1).strip() + " "
            text = bullet_match.group(2).strip()
        wrapped = textwrap.wrap(
            text,
            width=body_width - len(bullet_prefix),
            break_long_words=False,
            replace_whitespace=False,
        )
        if not wrapped:
            print()
            continue
        for i, part in enumerate(wrapped):
            prefix = bullet_prefix if i == 0 else " " * len(bullet_prefix)
            print(f"  {C_TEXT}{prefix}{part}{RESET}")

    print()
    print(f"  {C_BORDER}{'─' * (w - 4)}{RESET}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Prompt construction
# ─────────────────────────────────────────────────────────────────────────────

def _build_prompt_prefix(state: TUIState) -> str:
    """Build the styled prompt prefix."""
    backend_short = "cdx" if state.backend == "codex" else "oai"
    session_short = state.session_id.split("-")[-1][:6]
    return (
        f"{C_BRAND_BOLD}mtp{RESET}"
        f"{C_DIM}:{RESET}"
        f"{C_ACCENT_DIM}{backend_short}{RESET}"
        f"{C_DIM}:{RESET}"
        f"{C_VALUE}{session_short}{RESET}"
        f" {C_PROMPT_ARROW}❯{RESET} "
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
    print(f"  {C_DIM}Compose mode: paste/type multiple lines.{RESET}")
    print(f"  {C_DIM}Type /send on a new line to submit, or /cancel to abort.{RESET}")
    lines: list[str] = []
    while True:
        try:
            line = input(f"  {C_ACCENT_DIM}...{RESET} ")
        except (EOFError, KeyboardInterrupt):
            return None
        marker = line.strip().lower()
        if marker == "/cancel":
            return None
        if marker == "/send":
            break
        lines.append(line)
    composed = "\n".join(lines).strip()
    return composed or None


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
        last_usage_lines=[],
        transcript=[],
        session_store=session_store,
        session_id=initial_session_id,
        session_label=None,
        user_id="tui-user",
    )
    if args.session_id:
        existing = _load_session_record(state, args.session_id)
        if existing is not None:
            _load_session_into_state(state, existing)
    _save_tui_session(state)
    _print_banner(state)

    while True:
        try:
            prompt_prefix = _build_prompt_prefix(state)
            raw = input(prompt_prefix)
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C_DIM}Exiting TUI. Goodbye!{RESET}")
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
                    print(f"\n{C_BRAND}Goodbye!{RESET} ✨\n")
                    return 0
                if out:
                    print(f"  {out}")
                continue

        expanded_prompt, attachments, attachment_warnings = _collect_prompt_attachments(raw, state.cwd)
        active_model = (
            state.codex_model or "(codex-default)"
            if state.backend == "codex"
            else state.openai_model
        )

        print(
            f"  {C_DIM}Running on {state.backend} · model={active_model} · reasoning={state.reasoning_effort}{RESET}"
        )
        try:
            if state.backend == "codex":
                result = _run_codex_prompt(state, expanded_prompt)
                result.attachments = attachments
                result.warnings = [*attachment_warnings, *result.warnings]
            else:
                result = _run_openai_prompt(state, expanded_prompt)
                result.attachments = attachments
                result.warnings = [*attachment_warnings, *result.warnings]
        except Exception as exc:  # noqa: BLE001
            print(f"  {C_ERROR}✗ Error:{RESET} {exc}")
            continue
        _record_turn(state, raw, result)
        _render_prompt_and_response(result)
