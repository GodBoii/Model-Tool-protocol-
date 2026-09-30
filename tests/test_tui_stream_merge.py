from __future__ import annotations

from typing import Any, Iterator

from mtp.cli.tui_mtp_backend import _merge_stream_text, run_mtp_prompt


class _FakeAgent:
    """Minimal stand-in for MTPAgent that replays a fixed event list."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def run_events(self, **_: Any) -> Iterator[dict[str, Any]]:
        yield from self._events


def _text_events(chunks: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"type": "text_chunk", "chunk": c, "source": "direct"} for c in chunks]
    events.append({"type": "run_completed", "final_text": ""})
    return events


def test_merge_keeps_tokens_that_already_appeared() -> None:
    text = ""
    for chunk in ["The cat", " sat on", " the", " mat", ",", " the", " end", "."]:
        text = _merge_stream_text(text, chunk)
    assert text == "The cat sat on the mat, the end."


def test_merge_keeps_overlapping_suffix_prefix() -> None:
    # "in the" followed by " the" must not collapse into a single " the".
    assert _merge_stream_text("look in the", " the box") == "look in the the box"


def test_merge_accepts_cumulative_replay() -> None:
    first = "A long enough streamed answer that a provider may resend."
    assert _merge_stream_text(first, first + " More.") == first + " More."


def test_merge_ignores_exact_full_replay() -> None:
    full = "A long enough streamed answer that a provider may resend."
    assert _merge_stream_text(full, full) == full


def test_merge_short_repeat_is_not_treated_as_replay() -> None:
    assert _merge_stream_text("ha", "ha") == "haha"


def test_run_mtp_prompt_preserves_repeated_tokens() -> None:
    chunks = ["Hello", "\n", "\n", "- one", "\n", "- one", "\n", "done"]
    result = run_mtp_prompt(agent=_FakeAgent(_text_events(chunks)), prompt="x", max_rounds=1)
    expected = "".join(chunks)
    assert result.text == expected
    text_blocks = [b for b in result.assistant_blocks if b.get("type") == "text"]
    assert [b["text"] for b in text_blocks] == [expected]


def test_run_mtp_prompt_preserves_repeated_reasoning_tokens() -> None:
    events = [
        {"type": "reasoning_chunk", "chunk": "step"},
        {"type": "reasoning_chunk", "chunk": " step"},
        {"type": "reasoning_chunk", "chunk": " step"},
        {"type": "text_chunk", "chunk": "ok", "source": "direct"},
        {"type": "run_completed", "final_text": "ok"},
    ]
    result = run_mtp_prompt(agent=_FakeAgent(events), prompt="x", max_rounds=1)
    assert result.thinking_text == "step step step"
    thinking_blocks = [b for b in result.assistant_blocks if b.get("type") == "thinking"]
    assert thinking_blocks[0]["text"] == "step step step"
