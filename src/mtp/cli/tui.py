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
import threading
import time
from typing import Any

from mtp import Agent
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
    "gpt-5.2": 400_000,
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
        (f"{C_CMD}/model{RESET}", "Switch model"),
        (f"{C_CMD}/backend{RESET}", "Switch backend"),
        (f"{C_CMD}/reasoning{RESET}", "Set reasoning effort"),
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
            ("/status", "Show current session state"),
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


def _context_usage_line(model: str, input_tokens: int | None) -> str:
    window = _MODEL_CONTEXT_WINDOWS.get(model)
    if window is None:
        return f"context={_format_int(input_tokens)} tokens / window=unknown"
    if input_tokens is None:
        return f"context=unknown / window={window:,}"
    pct = (float(input_tokens) / float(window)) * 100.0
    return f"context={input_tokens:,}/{window:,} ({_format_pct(pct)})"


def _merge_usage_metrics(total: dict[str, int], usage: dict[str, Any]) -> None:
    for key in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


def _extract_rate_values(payload: Any, found: dict[str, Any]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).lower()
            if "rate" in key_l and "remaining" in key_l:
                found[str(key)] = value
            elif key_l in {
                "x-ratelimit-remaining-requests",
                "x-ratelimit-remaining-tokens",
                "remaining_requests",
                "remaining_tokens",
                "remaining",
            }:
                found[str(key)] = value
            _extract_rate_values(value, found)
    elif isinstance(payload, list):
        for item in payload:
            _extract_rate_values(item, found)


def _format_rate_line(rate_fields: dict[str, Any] | None) -> str:
    if not rate_fields:
        return "rate_remaining=unknown"
    normalized = {str(k).lower(): v for k, v in rate_fields.items()}
    req = normalized.get("x-ratelimit-remaining-requests") or normalized.get("remaining_requests")
    tok = normalized.get("x-ratelimit-remaining-tokens") or normalized.get("remaining_tokens")
    reset = (
        normalized.get("x-ratelimit-reset-requests")
        or normalized.get("x-ratelimit-reset-tokens")
        or normalized.get("retry-after")
    )
    pieces: list[str] = []
    if req is not None:
        pieces.append(f"requests={req}")
    if tok is not None:
        pieces.append(f"tokens={tok}")
    if reset is not None:
        pieces.append(f"reset={reset}")
    if pieces:
        return "rate_remaining=" + ", ".join(pieces)
    compact = ", ".join(f"{k}={v}" for k, v in sorted(rate_fields.items())[:4])
    return f"rate_remaining={compact}"


def _parse_codex_json_events(stdout_text: str, active_model: str | None) -> tuple[str, list[str], list[str], list[str]]:
    tool_events: list[str] = []
    warnings: list[str] = []
    usage_lines: list[str] = []
    final_text = ""
    assistant_chunks: list[str] = []
    total_usage: dict[str, int] = {}
    peak_input_tokens: int | None = None
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
            input_tokens = usage.get("input_tokens")
            if isinstance(input_tokens, int):
                peak_input_tokens = max(peak_input_tokens or 0, input_tokens)
        _extract_rate_values(event, rate_fields)
        event_type = str(event.get("type", "")).strip().lower()
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
        if "tool" in event_type or "exec_command" in event_type:
            name = event.get("name") or event.get("tool_name") or event_type
            detail = event.get("summary") or event.get("command") or ""
            if detail:
                tool_events.append(f"{name}: {detail}")
            else:
                tool_events.append(str(name))
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
    model_name = (active_model or "").strip().lower()
    if "gpt-5.4-mini" in model_name:
        usage_lines.append(_context_usage_line("gpt-5.4-mini", peak_input_tokens))
    elif "gpt-5.4" in model_name:
        usage_lines.append(_context_usage_line("gpt-5.4", peak_input_tokens))
    elif "gpt-5.2" in model_name:
        usage_lines.append(_context_usage_line("gpt-5.2", peak_input_tokens))
    elif "gpt-4o" in model_name:
        usage_lines.append(_context_usage_line("gpt-4o", peak_input_tokens))
    else:
        usage_lines.append(_context_usage_line("unknown", peak_input_tokens))

    remaining_pct: str | None = None
    for chunk in (stdout_text, "\n".join(warnings)):
        match = re.search(r"(\d{1,3})%\s+left", chunk, re.IGNORECASE)
        if match:
            remaining_pct = match.group(1) + "%"
            break
    if remaining_pct is not None:
        usage_lines.append(f"rate_remaining={remaining_pct}")
    else:
        usage_lines.append(_format_rate_line(rate_fields))

    return final_text, tool_events, warnings, usage_lines


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
    proc = subprocess.run(cmd, capture_output=True, text=True)
    text = ""
    try:
        if output_path.exists():
            text = output_path.read_text(encoding="utf-8", errors="replace").strip()
    finally:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass

    parsed_text, tool_events, parse_warnings, usage_lines = _parse_codex_json_events(
        proc.stdout or "",
        state.codex_model,
    )

    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        hint = "Try: codex login"
        if details:
            return ChatResult(
                text=f"Codex exec failed (exit {proc.returncode}).\n{details}\n{hint}",
                tool_events=tool_events,
                attachments=[],
                warnings=parse_warnings,
                usage_lines=usage_lines,
            )
        return ChatResult(
            text=f"Codex exec failed (exit {proc.returncode}).\n{hint}",
            tool_events=tool_events,
            attachments=[],
            warnings=parse_warnings,
            usage_lines=usage_lines,
        )

    if text:
        final_text = text
    elif parsed_text:
        final_text = parsed_text
    elif proc.stdout.strip():
        final_text = proc.stdout.strip()
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
    peak_input_tokens: int | None = None
    latest_rate_limits: dict[str, Any] | None = None

    for event in agent.run_events(prompt, max_rounds=state.max_rounds, stream_final=False):
        event_type = str(event.get("type", ""))
        if event_type == "llm_response":
            usage = event.get("usage")
            if isinstance(usage, dict):
                _merge_usage_metrics(totals, usage)
                input_tokens = usage.get("input_tokens")
                if isinstance(input_tokens, int):
                    peak_input_tokens = max(peak_input_tokens or 0, input_tokens)
            rate_limits = event.get("rate_limits")
            if isinstance(rate_limits, dict):
                latest_rate_limits = rate_limits
        elif event_type == "tool_started":
            tool_name = str(event.get("tool_name") or "tool")
            call_id = str(event.get("call_id") or "")
            if call_id:
                tool_events.append(f"{tool_name} ({call_id})")
            else:
                tool_events.append(tool_name)
        elif event_type == "tool_finished":
            if not bool(event.get("success", True)):
                tool_name = str(event.get("tool_name") or "tool")
                err = str(event.get("error") or "unknown tool error")
                warnings.append(f"{tool_name} failed: {err}")
        elif event_type == "run_completed":
            final_text = str(event.get("final_text") or "")

    usage_lines = [
        "tokens(in/out/total/reasoning)="
        f"{_format_int(totals.get('input_tokens'))}/"
        f"{_format_int(totals.get('output_tokens'))}/"
        f"{_format_int(totals.get('total_tokens'))}/"
        f"{_format_int(totals.get('reasoning_tokens'))}",
        _context_usage_line(state.openai_model, peak_input_tokens),
        _format_rate_line(latest_rate_limits),
    ]
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
    if cmd == "/exit":
        return "__exit__"
    if cmd == "/status":
        _print_status(state)
        return None
    if cmd == "/models":
        _print_model_matrix(state)
        return None
    if cmd == "/backend":
        if arg not in _BACKENDS:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/backend codex|mtp-openai{RESET}"
        state.backend = arg
        return f"{C_SUCCESS}✓{RESET} Switched backend to {C_VALUE}{arg}{RESET}."
    if cmd == "/model":
        if not arg:
            _print_model_matrix(state)
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/model <name|1..4|default>{RESET}"
        resolved = _resolve_model(arg)
        if state.backend == "codex":
            if resolved.lower() in {"default", "auto"}:
                state.codex_model = None
                return f"{C_SUCCESS}✓{RESET} Codex model reset to CLI default."
            state.codex_model = resolved
            return f"{C_SUCCESS}✓{RESET} Codex model set to {C_MODEL}{resolved}{RESET}."
        state.openai_model = resolved
        state.agent = None
        return f"{C_SUCCESS}✓{RESET} OpenAI model set to {C_MODEL}{resolved}{RESET}. Agent reloaded."
    if cmd == "/reasoning":
        if not arg:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/reasoning <none|low|medium|high|xhigh>{RESET}"
        resolved_reasoning = _resolve_reasoning(arg)
        if resolved_reasoning is None:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/reasoning <none|low|medium|high|xhigh>{RESET}"
        state.reasoning_effort = resolved_reasoning
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
        return f"{C_SUCCESS}✓{RESET} max_rounds set to {C_VALUE}{rounds}{RESET}."
    if cmd == "/autoresearch":
        lowered = arg.lower()
        if lowered not in {"on", "off"}:
            return f"{C_WARNING}Usage:{RESET} {C_CMD}/autoresearch on|off{RESET}"
        state.autoresearch = lowered == "on"
        state.agent = None
        return f"{C_SUCCESS}✓{RESET} autoresearch={C_VALUE}{state.autoresearch}{RESET}. Agent reloaded."
    if cmd == "/research":
        state.research_instructions = arg or None
        state.agent = None
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
        "status",
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

    # Indent the response body
    for line in result.text.splitlines():
        print(f"  {C_TEXT}{line}{RESET}")

    print()
    print(f"  {C_BORDER}{'─' * (w - 4)}{RESET}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Prompt construction
# ─────────────────────────────────────────────────────────────────────────────

def _build_prompt_prefix(state: TUIState) -> str:
    """Build the styled prompt prefix."""
    backend_short = "cdx" if state.backend == "codex" else "oai"
    return (
        f"{C_BRAND_BOLD}mtp{RESET}"
        f"{C_DIM}:{RESET}"
        f"{C_ACCENT_DIM}{backend_short}{RESET}"
        f" {C_PROMPT_ARROW}❯{RESET} "
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main Loop
# ─────────────────────────────────────────────────────────────────────────────

def run_tui(args) -> int:
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
    )
    _print_banner(state)

    while True:
        try:
            prompt_prefix = _build_prompt_prefix(state)
            raw = input(prompt_prefix).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C_DIM}Exiting TUI. Goodbye!{RESET}")
            return 0
        if not raw:
            continue
        raw = _normalize_input(raw)
        if raw.startswith("/"):
            out = _handle_command(state, raw)
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

        # Show running status with spinner
        spinner = _Spinner(
            f"Running on {state.backend}  ·  model={active_model}  ·  reasoning={state.reasoning_effort}"
        )
        spinner.start()
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
            spinner.stop()
            print(f"  {C_ERROR}✗ Error:{RESET} {exc}")
            continue
        spinner.stop()
        state.last_usage_lines = list(result.usage_lines)
        _render_prompt_and_response(result)
