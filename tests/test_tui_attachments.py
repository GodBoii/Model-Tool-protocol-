from __future__ import annotations

from pathlib import Path

from mtp.cli import tui_workers
from mtp.cli.tui_state import MAX_ATTACHMENT_CHARS
from mtp.cli.tui_workers import collect_prompt_attachments


def test_large_attachment_is_read_only_up_to_the_limit(tmp_path: Path, monkeypatch) -> None:
    big = tmp_path / "big.log"
    big.write_text("x" * (MAX_ATTACHMENT_CHARS * 20), encoding="utf-8")
    requested: list[int] = []
    real_read = tui_workers._read_attachment_text

    def spy(path: Path, limit: int) -> tuple[str, bool]:
        requested.append(limit)
        return real_read(path, limit)

    monkeypatch.setattr(tui_workers, "_read_attachment_text", spy)
    expanded, attachments, warnings = collect_prompt_attachments("see @big.log", tmp_path)
    assert attachments == ["big.log"]
    assert warnings == ["Truncated: big.log"]
    assert "x" * MAX_ATTACHMENT_CHARS + "\n```" in expanded
    assert "x" * (MAX_ATTACHMENT_CHARS + 1) not in expanded
    assert requested == [MAX_ATTACHMENT_CHARS]


def test_small_attachment_is_not_truncated(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    expanded, attachments, warnings = collect_prompt_attachments("read @a.txt", tmp_path)
    assert attachments == ["a.txt"]
    assert warnings == []
    assert "hello" in expanded


def test_read_attachment_text_reports_truncation(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("abcdef", encoding="utf-8")
    assert tui_workers._read_attachment_text(path, 6) == ("abcdef", False)
    assert tui_workers._read_attachment_text(path, 3) == ("abc", True)
