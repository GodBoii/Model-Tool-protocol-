"""Text accumulation shared by streaming clients."""
from __future__ import annotations


# Short repeats are ordinary tokens, not cumulative provider replays.
_REPLAY_MIN_CHARS = 32


def merge_stream_text(existing: str, incoming: str) -> str:
    """Append deltas, recognising only long full-payload replays.

    Exact repeats and cumulative snapshots must cover all existing text and
    reach the minimum length. Substrings and suffix overlaps are real deltas.
    """
    if not incoming:
        return existing
    if not existing:
        return incoming
    if len(existing) >= _REPLAY_MIN_CHARS:
        if incoming == existing:
            return existing
        if incoming.startswith(existing):
            return incoming
    return existing + incoming
