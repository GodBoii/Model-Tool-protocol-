"""TUI Completers — prompt_toolkit autocompletion and input styling.

This module provides:
- @-file autocomplete (cached, fast, smart directory pruning)
- /command autocomplete
- Merged completer combining both
- PromptSession builder with dark theme, toolbar, and keybindings
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .tui_theme import (
    COLOR_ENABLED,
    SYM_DOT, SYM_OK, SYM_V, SYM_PROMPT_ARROW,
    C_DIM, C_ACCENT_DIM, C_BRAND_BOLD, C_VALUE, C_PROMPT_ARROW as C_PA,
    get_term_width, strip_ansi,
)

# ── prompt_toolkit (optional, graceful fallback) ─────────────────────────────
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.styles import Style as PTKStyle
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False


# ─────────────────────────────────────────────────────────────────────────────
# File Completer
# ─────────────────────────────────────────────────────────────────────────────

# Directories to skip entirely during file scanning
_SKIP_DIRS = {
    "__pycache__", "node_modules", ".git", "venv", ".venv", ".env",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "site-packages", "dist-info", ".egg-info", ".eggs",
    "Lib", "Include", "Scripts", "lib", "include",
    "build", "dist", ".idea", ".vs", ".vscode",
    "htmlcov", "coverage", ".coverage",
}

if HAS_PROMPT_TOOLKIT:
    class AtFileCompleter(Completer):  # type: ignore[misc]
        """Autocomplete file paths after @ in prompts — cached & fast."""

        _MAX_DEPTH = 3
        _MAX_RESULTS = 25
        _CACHE_TTL = 5.0  # Seconds before re-scanning

        def __init__(self, cwd_fn):
            self.cwd_fn = cwd_fn
            self._cache: list[str] = []
            self._cache_cwd: Path | None = None
            self._cache_time: float = 0.0

        def _refresh_cache(self, cwd: Path) -> list[str]:
            """Rebuild file list only when CWD changes or cache expires."""
            now = time.monotonic()
            if (
                self._cache_cwd == cwd
                and self._cache
                and (now - self._cache_time) < self._CACHE_TTL
            ):
                return self._cache

            entries: list[str] = []
            self._walk(cwd, cwd, 0, entries)
            entries.sort(key=str.lower)
            self._cache = entries
            self._cache_cwd = cwd
            self._cache_time = now
            return entries

        def _walk(self, base: Path, current: Path, depth: int, out: list[str]) -> None:
            """Manual walk with depth limit and smart directory skipping."""
            if depth > self._MAX_DEPTH:
                return
            try:
                children = sorted(current.iterdir(), key=lambda p: p.name.lower())
            except (PermissionError, OSError):
                return
            for child in children:
                name = child.name
                if name.startswith("."):
                    continue
                if name in _SKIP_DIRS:
                    continue
                if name.endswith(".dist-info") or name.endswith(".egg-info"):
                    continue
                try:
                    rel = str(child.relative_to(base)).replace("\\", "/")
                except ValueError:
                    continue
                out.append(rel)
                if child.is_dir():
                    self._walk(base, child, depth + 1, out)

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            at_pos = text.rfind("@")
            if at_pos < 0:
                return
            if at_pos > 0 and text[at_pos - 1] not in (" ", "\t", "\n"):
                return
            partial = text[at_pos + 1:].lower().replace("\\", "/")
            raw_partial = text[at_pos + 1:]
            cwd = self.cwd_fn()
            entries = self._refresh_cache(cwd)
            count = 0
            for rel in entries:
                if count >= self._MAX_RESULTS:
                    break
                rel_lower = rel.lower()
                if rel_lower.startswith(partial) or (partial and partial in rel_lower):
                    full = cwd / rel.replace("/", os.sep)
                    meta = ""
                    try:
                        if full.is_dir():
                            meta = "dir"
                        else:
                            meta = self._file_size(full)
                    except (OSError, PermissionError):
                        pass
                    yield Completion(
                        rel,
                        start_position=-len(raw_partial),
                        display=rel,
                        display_meta=meta,
                    )
                    count += 1

        @staticmethod
        def _file_size(p: Path) -> str:
            try:
                size = p.stat().st_size
                if size < 1024:
                    return f"{size}B"
                if size < 1024 * 1024:
                    return f"{size // 1024}KB"
                return f"{size // (1024 * 1024)}MB"
            except (OSError, PermissionError):
                return ""


    class CommandCompleter(Completer):  # type: ignore[misc]
        """Autocomplete / slash commands."""

        _COMMANDS = [
            "/help", "/exit", "/compose", "/status", "/new", "/load",
            "/sessions", "/history", "/clear", "/cd", "/tools",
            "/backend", "/models", "/model", "/reasoning", "/rounds",
            "/autoresearch", "/research", "/codex-login",
        ]

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor.lstrip()
            if not text.startswith("/"):
                return
            if " " in text:
                return
            for cmd in self._COMMANDS:
                if cmd.startswith(text.lower()):
                    yield Completion(cmd, start_position=-len(text))


    class MergedCompleter(Completer):  # type: ignore[misc]
        """Merges @-file and /command completers."""

        def __init__(self, completers: list[Completer]):
            self.completers = completers

        def get_completions(self, document, complete_event):
            for completer in self.completers:
                yield from completer.get_completions(document, complete_event)


# ─────────────────────────────────────────────────────────────────────────────
# Dark Theme Style Dict
# ─────────────────────────────────────────────────────────────────────────────

DARK_THEME_STYLE = {
    # Bottom toolbar
    "bottom-toolbar":       "bg:#0e0e1a #6b6b80",
    "bottom-toolbar.text":  "bg:#0e0e1a #6b6b80",
    # Completion menu — dark with violet accent
    "completion-menu":                      "bg:#1a1a2e #b4aae0",
    "completion-menu.completion":            "bg:#1a1a2e #b4aae0",
    "completion-menu.completion.current":    "bg:#2d2b55 #e0daf8 bold",
    "completion-menu.meta":                  "bg:#1a1a2e #646478",
    "completion-menu.meta.current":          "bg:#2d2b55 #9d9db5",
    # Scrollbar
    "scrollbar.background":                 "bg:#1a1a2e",
    "scrollbar.button":                     "bg:#3d3d60",
    "scrollbar.arrow":                      "bg:#3d3d60",
    # Auto-suggest ghost text
    "auto-suggestion":                      "#3d3d50",
}


# ─────────────────────────────────────────────────────────────────────────────
# Prompt Prefix Builders
# ─────────────────────────────────────────────────────────────────────────────

def build_prompt_prefix_html(state) -> str:
    """Build prompt prefix using prompt_toolkit HTML formatting.
    
    Now simplified to just the arrow for use inside input box.
    """
    # Simple prompt arrow for inside the box
    return f'<style fg="#a78bfa">{SYM_PROMPT_ARROW}</style> '


def build_prompt_prefix_html_with_box(state) -> tuple[str, str, str]:
    """Build input box frame and prompt prefix for prompt_toolkit.
    
    Returns:
        (top_border, prompt_html, bottom_border)
    """
    from .tui_theme import input_box_top, input_box_bottom, get_term_width
    
    # Build session label for box header
    backend_short = "cdx" if state.backend == "codex" else "oai"
    session_short = state.session_id.split("-")[-1][:6]
    label = f"mtp:{backend_short}:{session_short}"
    
    w = get_term_width()
    top = input_box_top(width=w, label=label)
    bottom = input_box_bottom(width=w)
    
    # Prompt with vertical border
    prompt_html = f'<style fg="#4b4b64">│</style> <style fg="#a78bfa">{SYM_PROMPT_ARROW}</style> '
    
    return (top, prompt_html, bottom)


def build_bottom_toolbar(state) -> str:
    """Build the persistent bottom status toolbar (prompt_toolkit HTML format).

    Design: single muted palette, clean dot separators, readable shortcuts.
    """
    # Use the active model name
    if state.backend == "codex":
        model = state.codex_model or "(codex-default)"
    else:
        model = state.openai_model
    turns = len(state.transcript)
    reasoning = state.reasoning_effort
    backend = state.backend

    d = '#6b6b80'  # dim label
    v = '#9d9db5'  # value (slightly brighter, same hue)
    sep = f'<style fg="#3d3d50"> {SYM_DOT} </style>'

    return (
        f'<style fg="{v}">{model}</style>'
        f'{sep}'
        f'<style fg="{d}">reasoning </style><style fg="{v}">{reasoning}</style>'
        f'{sep}'
        f'<style fg="{d}">{backend}</style>'
        f'{sep}'
        f'<style fg="{d}">turns </style><style fg="{v}">{turns}</style>'
        f'<style fg="#3d3d50">  {SYM_V}  </style>'
        f'<style fg="{d}">^C </style><style fg="#7a7a95">stop</style>'
        f'<style fg="#3d3d50">  </style>'
        f'<style fg="{d}">^L </style><style fg="#7a7a95">clear</style>'
        f'<style fg="#3d3d50">  </style>'
        f'<style fg="{d}">^D </style><style fg="#7a7a95">exit</style>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# PromptSession Builder
# ─────────────────────────────────────────────────────────────────────────────

def build_prompt_session(state, banner_fn) -> "PromptSession | None":
    """Build a configured PromptSession, or None if prompt_toolkit is unavailable.

    Args:
        state: TUIState — provides cwd and other session info.
        banner_fn: Callable to redraw the banner on Ctrl+L.
    """
    if not HAS_PROMPT_TOOLKIT:
        return None

    try:
        history_path = Path.home() / ".mtp" / "history.txt"
        history_path.parent.mkdir(parents=True, exist_ok=True)

        # Keyboard bindings
        kb = KeyBindings()

        @kb.add("c-l")
        def _clear_screen(event):
            """Ctrl+L: Clear screen and redraw banner."""
            os.system("cls" if os.name == "nt" else "clear")
            banner_fn()

        completer = MergedCompleter([
            CommandCompleter(),
            AtFileCompleter(cwd_fn=lambda: state.cwd),
        ])

        ptk_style = PTKStyle.from_dict(DARK_THEME_STYLE)

        # Import placeholder support
        try:
            from prompt_toolkit.formatted_text import HTML as PTK_HTML
            placeholder_text = PTK_HTML('<style fg="#646478">Type your message or @file to attach</style>')
        except ImportError:
            placeholder_text = None

        return PromptSession(
            history=FileHistory(str(history_path)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=completer,
            complete_while_typing=False,  # Tab-triggered completions — no lag
            key_bindings=kb,
            style=ptk_style,
            mouse_support=False,
            placeholder=placeholder_text,
        )
    except Exception as exc:
        # Graceful fallback — print a warning and return None
        from .tui_theme import C_WARNING, RESET
        print(f"  {C_WARNING}prompt_toolkit unavailable ({exc}); using basic input mode.{RESET}")
        return None
