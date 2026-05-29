"""TUI Commands — CommandPalette provider + slash command handling.

Provides all TUI commands as searchable actions through Textual's
CommandPalette (Ctrl+P) and also handles legacy /command parsing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.command import Provider, Hits, Hit, DiscoveryHit

if TYPE_CHECKING:
    from .tui_app import MTPApp


# ── Command definitions ─────────────────────────────────────────────────────
# Each entry: (display_name, description, cmd, arg)

COMMANDS: list[tuple[str, str, str, str]] = [
    ("Help", "Show command reference", "help", ""),
    ("Exit", "Quit the TUI", "exit", ""),
    ("New Session", "Start a fresh chat session", "new", ""),
    ("Clear Screen", "Clear the chat log", "clear", ""),
    ("Status", "Show current session state", "status", ""),
    ("Sessions", "List saved chat sessions", "sessions", ""),
    ("History", "Show recent turns in this chat", "history", ""),
    ("Toggle Sidebar", "Show/hide workspace sidebar", "sidebar", ""),
    ("Models", "Show all available models", "models", ""),
    ("Tools", "Show tool events from last turn", "tools", ""),
    ("Tool Details", "Toggle expanded tool metadata", "details", "toggle"),
    ("Open Session", "Open a saved session transcript", "open", ""),
    ("Codebase Memory", "Enable or inspect project memory", "codebase", "memory"),
    # Reasoning
    ("Reasoning: None", "Set reasoning effort to none", "reasoning", "none"),
    ("Reasoning: Low", "Set reasoning effort to low", "reasoning", "low"),
    ("Reasoning: Medium", "Set reasoning effort to medium", "reasoning", "medium"),
    ("Reasoning: High", "Set reasoning effort to high", "reasoning", "high"),
    ("Reasoning: XHigh", "Set reasoning effort to extra high", "reasoning", "xhigh"),
    ("Thinking", "Open the thinking/reasoning control", "thinking", ""),
    # Mode
    ("Mode: Plan", "Set harness mode to plan", "mode", "plan"),
    ("Mode: Code", "Set harness mode to code", "mode", "code"),
    ("Mode: Debug", "Set harness mode to debug", "mode", "debug"),
    ("Mode: Review", "Set harness mode to review", "mode", "review"),
    # Sandbox
    ("Sandbox: Read Only", "Set to read-only", "sandbox", "read-only"),
    ("Sandbox: Workspace Write", "Set to workspace-write", "sandbox", "workspace-write"),
    ("Sandbox: Full Access", "DANGER: full access", "sandbox", "danger-full-access"),
    # Other
    ("Auto Research On", "Enable autoresearch", "autoresearch", "on"),
    ("Auto Research Off", "Disable autoresearch", "autoresearch", "off"),
    ("Codex Login", "Run official codex login flow", "codex-login", ""),
]


def _make_command(app, cmd: str, arg: str):
    """Create a no-arg callable that dispatches a command to the app."""
    async def _run() -> None:
        if cmd == "sidebar":
            app.action_toggle_sidebar()
        elif cmd == "clear":
            app.action_clear_chat()
        elif cmd == "exit":
            app.exit()
        else:
            app._dispatch_command(cmd, arg)
    return _run


class MTPCommandProvider(Provider):
    """Provides MTP TUI commands to the Textual CommandPalette."""

    async def discover(self) -> Hits:
        for name, desc, cmd, arg in COMMANDS:
            yield DiscoveryHit(
                display=name,
                command=_make_command(self.app, cmd, arg),
                help=desc,
            )

    async def search(self, query: str) -> Hits:
        query_lower = query.lower()
        for name, desc, cmd, arg in COMMANDS:
            matched = False
            score = 0.0
            if query_lower in name.lower():
                score = 1.0 if name.lower().startswith(query_lower) else 0.7
                matched = True
            elif query_lower in desc.lower():
                score = 0.4
                matched = True
            if matched:
                yield Hit(
                    score=score,
                    match_display=name,
                    command=_make_command(self.app, cmd, arg),
                    help=desc,
                )


# ── Slash command parsing ────────────────────────────────────────────────────

SLASH_COMMANDS = {
    "/help", "/exit", "/clear", "/status", "/sessions", "/history",
    "/models", "/tools", "/compose", "/thinking",
}

SLASH_WITH_ARG = {
    "/backend", "/model", "/apikey", "/reasoning", "/mode",
    "/rounds", "/autoresearch", "/research", "/cd", "/load",
    "/open", "/sandbox", "/new", "/history", "/codebase", "/details",
}


def parse_slash_command(raw: str) -> tuple[str, str] | None:
    """Parse a slash command into (command_name, arg) or None if not a command."""
    if not raw.startswith("/"):
        return None

    parts = raw.strip().split(" ", 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in SLASH_COMMANDS or cmd in SLASH_WITH_ARG:
        return cmd[1:], arg  # Strip the leading /

    command_heads = {
        "help", "exit", "compose", "new", "reset", "clear", "status",
        "history", "sessions", "load", "backend", "apikey", "models",
        "model", "reasoning", "thinking", "rounds", "codex-login", "autoresearch",
        "research", "cd", "tools", "details", "sandbox", "codebase", "open",
    }
    head = raw[1:].split(" ", 1)[0].strip().lower()
    if head in command_heads:
        return head, arg

    return "unknown", raw
