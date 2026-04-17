"""TUI Toast — Non-blocking notification system.

Provides lightweight, auto-dismissing toast notifications
that appear in the top-right corner of the terminal.
"""
from __future__ import annotations

import sys
import threading
import time

from .tui_theme import (
    COLOR_ENABLED,
    RESET, DIM,
    C_ACCENT, C_SUCCESS, C_WARNING, C_ERROR,
    SYM_INFO, SYM_OK, SYM_WARN, SYM_ERR, SYM_BULLET,
    get_term_width, strip_ansi,
)


def toast(msg: str, kind: str = "info", duration: float = 1.8) -> None:
    """Show a non-blocking top-right toast that auto-clears after `duration`.

    Args:
        msg: The text to display.
        kind: One of "info", "success", "warning", "error".
        duration: Seconds before the toast fades out.
    """
    if not COLOR_ENABLED:
        return
    colors = {
        "info": C_ACCENT,
        "success": C_SUCCESS,
        "warning": C_WARNING,
        "error": C_ERROR,
    }
    icons = {"info": SYM_INFO, "success": SYM_OK, "warning": SYM_WARN, "error": SYM_ERR}
    c = colors.get(kind, C_ACCENT)
    icon = icons.get(kind, SYM_BULLET)
    visible_text = f" {icon} {msg} "
    w = get_term_width()
    visible_len = len(strip_ansi(visible_text))
    target_x = max(1, w - visible_len - 1)

    def _animate_toast() -> None:
        frames = 6
        start_x = w
        # Glide in with ease-out cubic
        for i in range(frames):
            t = (i + 1) / frames
            t = 1 - (1 - t) ** 3
            current_x = int(start_x - (start_x - target_x) * t)
            draw_len = w - current_x
            if draw_len > 0:
                displayed_text = visible_text[:draw_len]
                sys.stdout.write(f"\033[s\033[1;{current_x}H{c}{DIM}{displayed_text}{RESET}\033[u")
                sys.stdout.flush()
            time.sleep(0.015)
            
        # Ensure full text is rendered securely
        sys.stdout.write(f"\033[s\033[1;{target_x}H{c}{DIM}{visible_text}{RESET}\033[u")
        sys.stdout.flush()

        time.sleep(duration)

        # Clear neatly
        sys.stdout.write(f"\033[s\033[1;{target_x}H{' ' * visible_len}\033[u")
        sys.stdout.flush()

    threading.Thread(target=_animate_toast, daemon=True).start()
