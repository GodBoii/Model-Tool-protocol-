from __future__ import annotations

from pathlib import Path

from mtp.cli.tui_workers import collect_prompt_attachments


def test_attachment_is_expanded_from_workspace(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")

    expanded, attachments, warnings = collect_prompt_attachments(
        "Review @notes.txt", tmp_path
    )

    assert attachments == ["notes.txt"]
    assert "[Attached file: notes.txt]" in expanded
    assert "hello" in expanded
    assert warnings == []


def test_attachment_outside_workspace_is_rejected_by_default(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("do not disclose", encoding="utf-8")

    expanded, attachments, warnings = collect_prompt_attachments(
        "Review @../secret.txt", workspace
    )

    assert expanded == "Review @../secret.txt"
    assert attachments == []
    assert warnings == ["Outside workspace: ../secret.txt"]
    assert "do not disclose" not in expanded


def test_external_attachment_requires_explicit_opt_in(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "public.txt"
    external.write_text("allowed", encoding="utf-8")

    expanded, attachments, warnings = collect_prompt_attachments(
        f"Review @{external}", workspace, allow_external=True
    )

    assert attachments == [str(external.resolve())]
    assert "allowed" in expanded
    assert warnings == []


def test_binary_attachment_is_not_added(tmp_path: Path) -> None:
    (tmp_path / "image.bin").write_bytes(b"PNG\x00\x01\x02payload")

    expanded, attachments, warnings = collect_prompt_attachments(
        "Inspect @image.bin", tmp_path
    )

    assert expanded == "Inspect @image.bin"
    assert attachments == []
    assert warnings == ["Binary file skipped: image.bin"]


def test_large_attachment_read_and_prompt_are_bounded(tmp_path: Path) -> None:
    from mtp.cli import tui_state

    (tmp_path / "large.txt").write_bytes(b"a" * (tui_state.MAX_ATTACHMENT_BYTES + 1000))

    expanded, attachments, warnings = collect_prompt_attachments(
        "Inspect @large.txt", tmp_path
    )

    assert attachments == ["large.txt"]
    assert warnings == ["Truncated: large.txt"]
    assert expanded.count("a") <= tui_state.MAX_ATTACHMENT_CHARS + 4


def test_duplicate_reference_is_only_read_once(tmp_path: Path) -> None:
    (tmp_path / "same.txt").write_text("once", encoding="utf-8")

    expanded, attachments, warnings = collect_prompt_attachments(
        "@same.txt then @same.txt", tmp_path
    )

    assert attachments == ["same.txt"]
    assert expanded.count("[Attached file:") == 1
    assert warnings == []


def test_aggregate_byte_limit_stops_further_reads(tmp_path: Path, monkeypatch) -> None:
    from mtp.cli import tui_state

    monkeypatch.setattr(tui_state, "MAX_ATTACHMENTS_TOTAL_BYTES", 5)
    (tmp_path / "first.txt").write_text("12345", encoding="utf-8")
    (tmp_path / "second.txt").write_text("should not be read", encoding="utf-8")

    expanded, attachments, warnings = collect_prompt_attachments(
        "@first.txt @second.txt", tmp_path
    )

    assert attachments == ["first.txt"]
    assert "12345" in expanded
    assert "should not be read" not in expanded
    assert warnings == [
        f"Attachment byte limit ({tui_state.MAX_ATTACHMENTS_TOTAL_BYTES}); skipping remaining files."
    ]
