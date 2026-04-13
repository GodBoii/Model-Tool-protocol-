"""TUI Theme — ANSI colors, symbols, and drawing primitives.

This module is the single source of truth for:
- Unicode / color capability detection
- ANSI escape sequences and semantic palette
- Box-drawing symbols (with ASCII fallbacks)
- Terminal drawing helpers (hrule, centered, strip_ansi, box_*)
"""
from __future__ import annotations

import os
import re
import sys


# ─────────────────────────────────────────────────────────────────────────────
# Console Setup (Windows UTF-8 + VT)
# ─────────────────────────────────────────────────────────────────────────────

def _setup_console() -> None:
    """Configure the console for UTF-8 and ANSI support on Windows."""
    if sys.platform == "win32":
        utf8_ready = False
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            set_ok = bool(kernel32.SetConsoleOutputCP(65001))
            current_cp = int(kernel32.GetConsoleOutputCP())
            utf8_ready = set_ok and current_cp == 65001
            if not utf8_ready:
                os.system("chcp 65001 >NUL")
                current_cp = int(kernel32.GetConsoleOutputCP())
                utf8_ready = current_cp == 65001
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            utf8_ready = False

        if utf8_ready:
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            except Exception:
                pass
        else:
            try:
                sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
            except Exception:
                pass


_setup_console()


# ─────────────────────────────────────────────────────────────────────────────
# Unicode Detection
# ─────────────────────────────────────────────────────────────────────────────

def _supports_unicode() -> bool:
    """Detect whether the terminal can render Unicode box-drawing characters."""
    if os.environ.get("MTP_TUI_ASCII", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    try:
        encoding = (sys.stdout.encoding or "").lower()
        if encoding not in {"utf-8", "utf8", "utf_8"}:
            return False
        if sys.platform == "win32":
            try:
                import ctypes
                cp = int(ctypes.windll.kernel32.GetConsoleOutputCP())  # type: ignore[attr-defined]
                if cp != 65001:
                    return False
            except Exception:
                return False
        return True
    except Exception:
        return False


UNICODE_ENABLED = _supports_unicode()


# ─────────────────────────────────────────────────────────────────────────────
# Symbols (Unicode with ASCII fallbacks)
# ─────────────────────────────────────────────────────────────────────────────

SYM_RULE         = "─" if UNICODE_ENABLED else "-"
SYM_V            = "│" if UNICODE_ENABLED else "|"
SYM_TL           = "╭" if UNICODE_ENABLED else "+"
SYM_TR           = "╮" if UNICODE_ENABLED else "+"
SYM_BL           = "╰" if UNICODE_ENABLED else "+"
SYM_BR           = "╯" if UNICODE_ENABLED else "+"
SYM_ML           = "├" if UNICODE_ENABLED else "+"
SYM_MR           = "┤" if UNICODE_ENABLED else "+"
SYM_DOT          = "·" if UNICODE_ENABLED else "|"
SYM_FILLED       = "●" if UNICODE_ENABLED else "*"
SYM_EMPTY        = "○" if UNICODE_ENABLED else "o"
SYM_PROMPT_ARROW = "❯" if UNICODE_ENABLED else ">"
SYM_INFO         = "ℹ" if UNICODE_ENABLED else "i"
SYM_WARN         = "⚠" if UNICODE_ENABLED else "!"
SYM_OK           = "✓" if UNICODE_ENABLED else "OK"
SYM_ERR          = "✗" if UNICODE_ENABLED else "x"
SYM_BULLET       = "•" if UNICODE_ENABLED else "-"


# ─────────────────────────────────────────────────────────────────────────────
# ANSI Color Support Detection
# ─────────────────────────────────────────────────────────────────────────────

def _supports_color() -> bool:
    """Detect whether the terminal supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if sys.platform == "win32":
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


COLOR_ENABLED = _supports_color()


def _sgr(code: str) -> str:
    """Return an ANSI SGR escape if color is enabled, else empty string."""
    return f"\033[{code}m" if COLOR_ENABLED else ""


# ─────────────────────────────────────────────────────────────────────────────
# Core Escape Sequences
# ─────────────────────────────────────────────────────────────────────────────

RESET       = _sgr("0")
BOLD        = _sgr("1")
DIM         = _sgr("2")
ITALIC      = _sgr("3")
UNDERLINE   = _sgr("4")


# ─────────────────────────────────────────────────────────────────────────────
# Color Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fg256(n: int) -> str:
    return _sgr(f"38;5;{n}")

def _bg256(n: int) -> str:
    return _sgr(f"48;5;{n}")

def _fg_rgb(r: int, g: int, b: int) -> str:
    return _sgr(f"38;2;{r};{g};{b}")

def _bg_rgb(r: int, g: int, b: int) -> str:
    return _sgr(f"48;2;{r};{g};{b}")


# ─────────────────────────────────────────────────────────────────────────────
# Semantic Palette
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# Drawing Primitives
# ─────────────────────────────────────────────────────────────────────────────

def get_term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except (ValueError, OSError):
        return 80


def strip_ansi(s: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", s)


def hrule(char: str = SYM_RULE, color: str = C_BORDER) -> str:
    w = get_term_width()
    return f"{color}{char * w}{RESET}"


def centered(text: str, width: int | None = None, pad_char: str = " ") -> str:
    """Center raw text (strips ANSI for width calc)."""
    w = width or get_term_width()
    visible = strip_ansi(text)
    padding = max(0, (w - len(visible)) // 2)
    return pad_char * padding + text


def box_line(content: str, width: int | None = None) -> str:
    """Render a line inside a box with vertical borders."""
    w = (width or get_term_width()) - 4
    visible_len = len(strip_ansi(content))
    pad = max(0, w - visible_len)
    return f"{C_BORDER}{SYM_V}{RESET} {content}{' ' * pad} {C_BORDER}{SYM_V}{RESET}"


def box_top(width: int | None = None) -> str:
    w = (width or get_term_width()) - 2
    return f"{C_BORDER}{SYM_TL}{SYM_RULE * w}{SYM_TR}{RESET}"


def box_bottom(width: int | None = None) -> str:
    w = (width or get_term_width()) - 2
    return f"{C_BORDER}{SYM_BL}{SYM_RULE * w}{SYM_BR}{RESET}"


def box_separator(width: int | None = None) -> str:
    w = (width or get_term_width()) - 2
    return f"{C_BORDER}{SYM_ML}{SYM_RULE * w}{SYM_MR}{RESET}"


def shorten_text(text: str, limit: int = 160) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def input_box_top(width: int | None = None, label: str | None = None) -> str:
    """Render the top border of an input box, optionally with a label.
    
    Examples:
        ╭─────────────────────────────────────────╮
        ╭─ mtp:cdx:b87744 ─────────────────────────╮
    """
    w = (width or get_term_width()) - 2
    if label:
        # Calculate space for label with padding
        label_text = f" {label} "
        label_len = len(strip_ansi(label_text))
        if label_len + 4 < w:  # Ensure we have room
            rule_after = w - label_len - 1
            return f"{C_BORDER}{SYM_TL}{SYM_RULE}{label_text}{SYM_RULE * rule_after}{SYM_TR}{RESET}"
    # No label or not enough space
    return f"{C_BORDER}{SYM_TL}{SYM_RULE * w}{SYM_TR}{RESET}"


def input_box_bottom(width: int | None = None) -> str:
    """Render the bottom border of an input box."""
    w = (width or get_term_width()) - 2
    return f"{C_BORDER}{SYM_BL}{SYM_RULE * w}{SYM_BR}{RESET}"
