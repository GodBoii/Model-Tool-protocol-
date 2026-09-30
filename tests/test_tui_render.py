"""Rendering behaviour of the chat log: incremental updates and history windowing."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("textual")

from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_state import TranscriptTurn, now_label
from mtp.cli.tui_widgets.chat_log import (
    HISTORY_PAGE,
    HISTORY_WINDOW,
    AssistantMessageWidget,
    ChatLog,
    ToolCallWidget,
    UserMessageWidget,
)
from mtp.cli.tui_widgets.stream_markdown import StreamingMarkdown

from test_tui_app_lifecycle import FakeRunner, _make_state, fake_runner  # noqa: F401  (fixture)


def _turn(index: int) -> TranscriptTurn:
    return TranscriptTurn(
        prompt=f"question {index}", response=f"answer {index}", backend="codex", model="gpt-test",
        attachments=[], warnings=[], usage_lines=[], created_at=now_label(),
    )


def test_streaming_updates_reuse_widgets(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("hi")
            run = await fake_runner.wait_started()
            emit = run["emit"]
            await asyncio.to_thread(emit, "tool_detail", {
                "type": "tool_started", "tool_name": "fs.read", "call_id": "c1", "started_at_ms": 1,
            })
            await asyncio.to_thread(emit, "text", "First paragraph.\n\n")
            await pilot.pause(0.2)

            chat_log = app.query_one("#chat-log", ChatLog)
            live = chat_log._live_widget
            assert live is not None
            tool = live.query_one(ToolCallWidget)
            markdown = live.query_one(StreamingMarkdown)
            assert chat_log.spinner_clock.active

            await asyncio.to_thread(emit, "text", "Second")
            await asyncio.to_thread(emit, "tool_detail", {
                "type": "tool_finished", "tool_name": "fs.read", "call_id": "c1", "success": True,
                "finished_at_ms": 5, "result_preview": "ok",
            })
            await pilot.pause(0.2)
            assert live.query_one(ToolCallWidget) is tool
            assert live.query_one(StreamingMarkdown) is markdown
            assert markdown.text == "First paragraph.\n\nSecond"
            assert not chat_log.spinner_clock.active

            run["release"].set()
            await pilot.pause(0.3)
            # The live widget becomes the finished turn instead of being rebuilt.
            assert chat_log._live_widget is None
            assert live.is_mounted
            assert len(list(chat_log.query(AssistantMessageWidget))) == 1

    asyncio.run(scenario())


def test_completing_a_turn_does_not_rebuild_history(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        state = _make_state(tmp_path)
        state.transcript = [_turn(0)]
        app = MTPApp(state=state)
        async with app.run_test(size=(120, 40)) as pilot:
            chat_log = app.query_one("#chat-log", ChatLog)
            first_user = chat_log.query(UserMessageWidget).first()
            app._send_prompt("next")
            run = await fake_runner.wait_started()
            run["release"].set()
            await pilot.pause(0.3)
            users = list(chat_log.query(UserMessageWidget))
            assert users[0] is first_user
            assert len(users) == 2
            # The in-flight banner is removed once the turn is saved.
            texts = [str(w.render()) for w in chat_log.query("SystemMessageWidget")]
            assert not any("mode=" in text for text in texts)

    asyncio.run(scenario())


def test_long_history_mounts_a_window_and_loads_more(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        state = _make_state(tmp_path)
        state.transcript = [_turn(i) for i in range(70)]
        app = MTPApp(state=state)
        async with app.run_test(size=(120, 40)) as pilot:
            chat_log = app.query_one("#chat-log", ChatLog)
            await pilot.pause()

            def mounted_prompts() -> list[str]:
                return [w._text for w in chat_log.query(UserMessageWidget)]

            assert len(mounted_prompts()) == HISTORY_WINDOW
            assert mounted_prompts()[-1] == "question 69"
            assert chat_log.hidden_turn_count == 70 - HISTORY_WINDOW

            chat_log.show_earlier()
            await pilot.pause()
            assert len(mounted_prompts()) == HISTORY_WINDOW + HISTORY_PAGE
            assert mounted_prompts()[0] == f"question {70 - HISTORY_WINDOW - HISTORY_PAGE}"

            while chat_log.hidden_turn_count:
                chat_log.show_earlier()
            await pilot.pause()
            prompts = mounted_prompts()
            assert prompts == [f"question {i}" for i in range(70)]
            assert chat_log.hidden_turn_count == 0
            assert not list(chat_log.query("EarlierTurnsButton"))

    asyncio.run(scenario())
