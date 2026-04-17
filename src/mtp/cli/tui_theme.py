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
def _supports_nerd_font() -> bool:
    val = os.environ.get("NF", "") or os.environ.get("NERD_FONT", "")
    return val.strip().lower() in {"1", "true", "yes", "on"}

NERD_FONT_ENABLED = _supports_nerd_font() and UNICODE_ENABLED

if NERD_FONT_ENABLED:
    SYM_TL           = "╭"
    SYM_TR           = "╮"
    SYM_BL           = "╰"
    SYM_BR           = "╯"
    SYM_ML           = "├"
    SYM_MR           = "┤"
    SYM_DOT          = "·"
    SYM_FILLED       = "󰚩"
    SYM_EMPTY        = "󰈈"
    SYM_PROMPT_ARROW = ""
    SYM_INFO         = ""
    SYM_WARN         = ""
    SYM_OK           = ""
    SYM_ERR          = ""
    SYM_BULLET       = ""
else:
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
# A rich immersive Synthwave / Cyberpunk neon gradient feel.

C_BRAND         = _fg_rgb(192, 132, 252)   # Neon Purple — primary brand
C_BRAND_BOLD    = BOLD + _fg_rgb(192, 132, 252)
C_ACCENT        = _fg_rgb(56, 189, 248)    # Electric Neon Cyan — accent
C_ACCENT_DIM    = _fg_rgb(14, 116, 144)    # Muted Cyan
C_SUCCESS       = _fg_rgb(52, 211, 153)    # Mint Green   — success/active
C_WARNING       = _fg_rgb(251, 191, 36)    # Radiant Amber— warnings
C_ERROR         = _fg_rgb(244, 63, 94)     # Neon Rose    — errors
C_DIM           = _fg_rgb(113, 113, 122)   # Zinc 500     — secondary text
C_TEXT          = _fg_rgb(244, 244, 246)   # Pure Off-White— body text
C_LABEL         = _fg_rgb(167, 139, 250)   # Lavender     — labels
C_HIGHLIGHT     = BOLD + _fg_rgb(255, 255, 255)  # Bright white
C_MODEL         = _fg_rgb(250, 204, 21)    # Gold         — model names
C_CMD           = _fg_rgb(129, 140, 248)   # Indigo       — commands
C_KEY           = _fg_rgb(216, 180, 254)   # Pink Purple  — keyboard shortcuts
C_VALUE         = _fg_rgb(45, 212, 191)    # Vivid Teal   — values
C_BORDER        = _fg_rgb(63, 63, 70)      # Zinc 700     — structural borders
C_PROMPT_ARROW  = BOLD + _fg_rgb(236, 72, 153)   # Hot Pink     — prompt
C_RESPONSE      = BOLD + _fg_rgb(139, 92, 246)   # Deep Violet  — assistant label


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


def gradient_progress_bar(pct: float, width: int = 20) -> str:
    """Render a gradient progress bar based on percentage (0.0 to 100.0)."""
    # Clamp percentage
    pct = max(0.0, min(100.0, pct))
    
    filled_chars = int((pct / 100.0) * width)
    empty_chars = width - filled_chars
    
    char_fill = "▆" if UNICODE_ENABLED else "="
    char_empty = "░" if UNICODE_ENABLED else "-"
    
    # Gradient starts at Mint Green (C_SUCCESS), through Amber (C_WARNING), to Neon Rose (C_ERROR)
    # 52, 211, 153 -> 251, 191, 36 -> 244, 63, 94
    
    if pct < 50.0:
        # Interpolate Green to Amber
        t = pct / 50.0
        r = int(52 + t * (251 - 52))
        g = int(211 + t * (191 - 211))
        b = int(153 + t * (36 - 153))
    else:
        # Interpolate Amber to Red
        t = (pct - 50.0) / 50.0
        r = int(251 + t * (244 - 251))
        g = int(191 + t * (63 - 191))
        b = int(36 + t * (94 - 36))
        
    color_ansi = _fg_rgb(r, g, b)
    
    return f"{color_ansi}{char_fill * filled_chars}{RESET}{C_BORDER}{char_empty * empty_chars}{RESET}"
