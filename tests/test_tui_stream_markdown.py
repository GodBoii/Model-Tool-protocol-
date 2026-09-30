from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from mtp.cli.tui_widgets.stream_markdown import StreamingMarkdown, stable_prefix_end


def test_no_break_means_no_stable_prefix() -> None:
    assert stable_prefix_end("hello world") == 0
    assert stable_prefix_end("hello\nworld") == 0


def test_prefix_ends_after_last_blank_line() -> None:
    text = "one\n\ntwo\n\nthr"
    assert text[: stable_prefix_end(text)] == "one\n\ntwo\n\n"


def test_blank_lines_inside_code_fence_are_not_breaks() -> None:
    text = "intro\n\n```python\na = 1\n\nb = 2\n"
    assert text[: stable_prefix_end(text)] == "intro\n\n"


def test_closed_fence_allows_later_break() -> None:
    text = "```\ncode\n\nmore\n```\n\nafter"
    assert text[: stable_prefix_end(text)] == "```\ncode\n\nmore\n```\n\n"


def test_tilde_fence_is_not_closed_by_backticks() -> None:
    text = "~~~\nx\n```\n\ny\n"
    assert stable_prefix_end(text) == 0


def test_unterminated_trailing_blank_is_ignored() -> None:
    # The final line has no newline yet, so it may still grow.
    assert stable_prefix_end("a\n\n  ") == len("a\n\n")


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield StreamingMarkdown(id="md")


def test_widget_freezes_segments_and_rerenders_only_tail() -> None:
    async def scenario() -> None:
        app = _Host()
        async with app.run_test() as pilot:
            md = app.query_one("#md", StreamingMarkdown)
            text = ""
            for token in ["Para one", ".\n", "\n", "Para two", ".\n\n", "tail"]:
                text += token
                md.update_text(text)
            await pilot.pause()
            assert len(md._segments) == 2
            assert md._tail_source == "tail"
            first_segment = md._segments[0]

            md.update_text(text + " grows")
            await pilot.pause()
            assert md._segments[0] is first_segment
            assert md._tail_source == "tail grows"

            md.update_text("replaced")
            await pilot.pause()
            assert md._segments == []
            assert md.text == "replaced"

    asyncio.run(scenario())


class _PrefilledHost(App[None]):
    def compose(self) -> ComposeResult:
        yield StreamingMarkdown("Intro.\n\nBody", id="md")


def test_initial_text_renders_on_mount() -> None:
    async def scenario() -> None:
        app = _PrefilledHost()
        async with app.run_test() as pilot:
            await pilot.pause()
            md = app.query_one("#md", StreamingMarkdown)
            assert md.text == "Intro.\n\nBody"
            assert len(md._segments) == 1
            assert md._tail_source == "Body"

    asyncio.run(scenario())
