import pytest

from mtp.cli.tui_limits import TUILimits, append_bounded, bounded_detail, bounded_tail, trim_display_blocks
from mtp.cli.tui_state import normalize_tui_rounds


def test_append_bounded_retains_newest_items() -> None:
    items = [1, 2]
    append_bounded(items, 3, 2)
    assert items == [2, 3]


@pytest.mark.parametrize("value", [0, -1, 101, "many", None])
def test_tui_rounds_are_bounded(value) -> None:
    with pytest.raises(ValueError, match="rounds"):
        normalize_tui_rounds(value)


def test_bounded_tail_marks_omitted_preview() -> None:
    result = bounded_tail("x" * 100, 50)
    assert len(result) == 50
    assert "omitted" in result
    assert result.endswith("x")


def test_bounded_detail_copies_and_limits_large_fields() -> None:
    original = {"tool_name": "shell", "result_preview": "a" * 100}
    result = bounded_detail(original, 40)
    assert result is not original
    assert len(result["result_preview"]) == 40
    assert original["result_preview"] == "a" * 100


def test_limits_from_env_falls_back_and_clamps(monkeypatch) -> None:
    monkeypatch.setenv("MTP_TUI_INPUT_HISTORY", "3")
    monkeypatch.setenv("MTP_TUI_LIVE_EVENTS", "invalid")
    monkeypatch.setenv("MTP_TUI_LIVE_WARNINGS", "0")
    monkeypatch.setenv("MTP_TUI_TRANSCRIPT_TURNS", "7")
    limits = TUILimits.from_env()
    assert limits.input_history == 3
    assert limits.live_events == 200
    assert limits.live_warnings == 1
    assert limits.transcript_turns == 7


def test_trim_display_blocks_bounds_aggregate_payload() -> None:
    blocks = [{"type": "text", "text": "a" * 20}, {"type": "text", "text": "b" * 20}]
    trim_display_blocks(blocks, max_blocks=10, max_chars=25)
    assert blocks == [{"type": "text", "text": "b" * 20}]
