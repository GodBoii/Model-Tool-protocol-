"""Keyboard, palette, and layout regression checks without any chat runs."""
from __future__ import annotations
import asyncio

import pytest
from textual.widgets import Input, OptionList, RichLog

from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_commands import COMMANDS, parse_slash_command
from mtp.cli.tui_widgets.input_area import InputArea
from mtp.cli.tui_widgets.thinking_dialog import ThinkingDialog
from mtp.cli.tui_workers import collect_prompt_attachments
from test_tui_app_lifecycle import _make_state


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (60, 20), (40, 15), (160, 50)])
def test_home_commands_fit_between_tabs_and_composer(tmp_path, size):
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=size) as pilot:
            await pilot.pause(.05)
            assert app.query_one("#conversation-tabs").region.y == 0
            app._dispatch_command("status", "")
            await pilot.pause(.05)
            assert not app.query_one("#boot-screen").display
            log = app.query_one("#cmd-log", RichLog)
            panel = app.query_one("#input-panel")
            assert log.region.bottom <= panel.region.y
            assert log.region.y >= app.query_one("#conversation-tabs").region.bottom
            status = app.query_one("#status-main")
            assert status.region.y < size[1]
            assert status.region.bottom <= size[1]
            assert log.lines
            assert log.virtual_size.width <= log.scrollable_content_region.width
            await pilot.press("ctrl+o")
            assert app.focused is log
            await pilot.press("pagedown", "escape")
            assert isinstance(app.focused, InputArea)
            assert not log.has_class("visible")
            app._dispatch_command("help", "")
            await pilot.resize_terminal(80, 24)
            await pilot.pause(.05)
            assert log.region.bottom <= panel.region.y
            assert log.virtual_size.width <= log.scrollable_content_region.width
    asyncio.run(scenario())


def test_multiline_editor_shortcuts_and_chat_drafts_are_independent(tmp_path):
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(100, 32)) as pilot:
            area = app.query_one(InputArea)
            area.focus()
            area.load_text("First line")
            area.cursor_location = (0, 10)
            await pilot.press("shift+enter")
            await pilot.press(*list("Second line"))
            assert area.text == "First line\nSecond line"
            sandbox = app.state.codex_sandbox_mode
            await pilot.press("ctrl+w")
            assert area.text != "First line\nSecond line"
            assert app.state.codex_sandbox_mode == sandbox
            draft = area.text
            app._pending_attachments.append("first.txt")
            first = app.active_conversation
            await pilot.press("ctrl+n")
            second = app.active_conversation
            assert area.text == "" and app._pending_attachments == []
            area.load_text("second draft")
            await pilot.press("f1")
            assert app.active_conversation is first
            assert area.text == draft and app._pending_attachments == ["first.txt"]
            await pilot.press("f2")
            assert app.active_conversation is second
            assert area.text == "second draft" and app._pending_attachments == []
            await pilot.press("alt+left")
            assert app.active_conversation is first
    asyncio.run(scenario())


def test_palette_new_chat_mounts_without_crashing(tmp_path):
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause(.1)
            assert isinstance(app.focused, Input)
            app.focused.value = "New Chat"
            await pilot.pause(.2)
            await pilot.press("enter")
            await pilot.pause(.15)
            assert len(app.conversations) == 2
            assert app.active_chat_log.query_one("#chat-log-body") is not None
            assert app._exception is None
    asyncio.run(scenario())


def test_all_nine_direct_chat_shortcuts(tmp_path):
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(100, 32)) as pilot:
            for _ in range(8):
                await pilot.press("ctrl+n")
            conversations = app.conversations
            for number, conversation in enumerate(conversations, 1):
                await pilot.press(f"f{number}")
                assert app.active_conversation is conversation
    asyncio.run(scenario())


def test_background_memory_notice_does_not_replace_chat_or_home(tmp_path):
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(100, 32)) as pilot:
            app._append_cmd_log("Background memory refresh complete")
            await pilot.pause(.05)
            assert app.query_one("#boot-screen").display
            assert not app.query_one("#cmd-log").has_class("visible")
            app._dispatch_command("codebase", "status")
            app._append_cmd_log("Scan complete")
            await pilot.pause(.05)
            assert app.query_one("#cmd-log").has_class("visible")
    asyncio.run(scenario())


def test_thinking_has_one_visible_command_and_opens_picker(tmp_path):
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(100, 32)) as pilot:
            app._show_command_suggestions("/")
            labels = [str(option.prompt) for option in app.query_one(OptionList).options]
            assert any(label.startswith("/thinking ") for label in labels)
            assert not any(label.startswith("/reasoning ") for label in labels)
            assert sum(cmd == "thinking" for name, desc, cmd, arg in COMMANDS) == 1
            assert not any(cmd == "reasoning" for name, desc, cmd, arg in COMMANDS)
            app._dispatch_command("thinking", "")
            await pilot.pause(.1)
            assert isinstance(app.screen, ThinkingDialog)
            await pilot.press("escape")
    asyncio.run(scenario())


@pytest.mark.parametrize("cmd,arg", [("rounds", "²"), ("history", "²"), ("switch", "²"), ("close", "²"), ("codex", '"'), ("rounds", "9" * 4400), ("sandbox", "wrong")])
def test_bad_arguments_report_errors_and_keep_app_alive(tmp_path, cmd, arg):
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(100, 32)) as pilot:
            app._dispatch_command(cmd, arg)
            await pilot.pause(.05)
            assert app._exception is None
            assert app.query_one("#cmd-log", RichLog).lines
            assert app.state.codex_sandbox_mode == "workspace-write"
    asyncio.run(scenario())


def test_relative_cd_model_add_and_quoted_attachment(tmp_path):
    (tmp_path / "space folder").mkdir()
    (tmp_path / "file with spaces.txt").write_text("attachment fixture", encoding="utf-8")
    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(100, 32)) as pilot:
            app._dispatch_command("cd", '"space folder"')
            assert app.state.cwd == tmp_path / "space folder"
            app._dispatch_command("cd", "..")
            assert app.state.cwd == tmp_path
            app._dispatch_command("model", "add groq custom-model")
            assert app.state.codex_model == "gpt-test"
            app._dispatch_command("autoresearch", "on")
            app._dispatch_command("autoresearch", "wrong")
            assert app.state.autoresearch
    asyncio.run(scenario())
    text, paths, warnings = collect_prompt_attachments('@"file with spaces.txt"', tmp_path)
    assert paths == ["file with spaces.txt"] and not warnings
    assert "attachment fixture" in text
    assert parse_slash_command("/mode\tplan") == ("mode", "plan")
