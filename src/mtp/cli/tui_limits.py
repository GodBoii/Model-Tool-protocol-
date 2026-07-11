"""Memory bounds for ephemeral TUI data.

These limits apply only to interactive history and live preview buffers.  Session
transcripts are deliberately not modified, so saving/reloading a session remains
lossless.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class TUILimits:
    input_history: int = 200
    live_text_chars: int = 200_000
    live_thinking_chars: int = 80_000
    live_events: int = 200
    live_details: int = 200
    live_warnings: int = 100
    live_blocks: int = 300
    tool_preview_chars: int = 8_000

    @classmethod
    def from_env(cls) -> "TUILimits":
        return cls(
            input_history=_env_int("MTP_TUI_INPUT_HISTORY", 200),
            live_text_chars=_env_int("MTP_TUI_LIVE_TEXT_CHARS", 200_000),
            live_thinking_chars=_env_int("MTP_TUI_LIVE_THINKING_CHARS", 80_000),
            live_events=_env_int("MTP_TUI_LIVE_EVENTS", 200),
            live_details=_env_int("MTP_TUI_LIVE_DETAILS", 200),
            live_warnings=_env_int("MTP_TUI_LIVE_WARNINGS", 100),
            live_blocks=_env_int("MTP_TUI_LIVE_BLOCKS", 300),
            tool_preview_chars=_env_int("MTP_TUI_TOOL_PREVIEW_CHARS", 8_000),
        )


def append_bounded(items: list[Any], item: Any, limit: int) -> None:
    """Append while retaining only the newest ``limit`` entries."""
    items.append(item)
    overflow = len(items) - limit
    if overflow > 0:
        del items[:overflow]


def bounded_tail(text: str, limit: int) -> str:
    """Keep a bounded suffix and make preview truncation visible."""
    if len(text) <= limit:
        return text
    marker = "\n… earlier live preview omitted …\n"
    if limit <= len(marker):
        return text[-limit:]
    return marker + text[-(limit - len(marker)):]


def bounded_detail(detail: dict[str, Any], preview_chars: int) -> dict[str, Any]:
    """Copy a tool detail while bounding potentially huge display fields."""
    copied = dict(detail)
    for key in ("result_preview", "error", "reasoning", "message"):
        value = copied.get(key)
        if isinstance(value, str):
            copied[key] = bounded_tail(value, preview_chars)
    return copied


def trim_display_blocks(blocks: list[dict[str, Any]], *, max_blocks: int, max_chars: int) -> None:
    """Bound a live block collection by count and approximate string payload."""
    overflow = len(blocks) - max_blocks
    if overflow > 0:
        del blocks[:overflow]

    def payload_chars(value: Any) -> int:
        if isinstance(value, str):
            return len(value)
        if isinstance(value, dict):
            return sum(payload_chars(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return sum(payload_chars(item) for item in value)
        return 0

    total = sum(payload_chars(block) for block in blocks)
    while len(blocks) > 1 and total > max_chars:
        total -= payload_chars(blocks.pop(0))
