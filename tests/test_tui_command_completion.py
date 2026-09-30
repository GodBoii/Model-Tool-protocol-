from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import OptionList

from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_widgets.input_area import InputArea
from test_tui_app_lifecycle import _make_state


@pytest.mark.parametrize(("text", "choice", "expected"), [
    ("/backend ", "groq", ("backend", "groq")),
    ("/backend gr", "groq", ("backend", "groq")),
    ("/backend groq ", "groq", ("backend", "groq")),
    ("/mode ", "review", ("mode", "review")),
    ("/sandbox ", "read-only", ("sandbox", "read-only")),
    ("/codex ", "account", ("codex", "account")),
    ("/codebase memory ", "on", ("codebase", "memory on")),
    ("/codebase Memory ", "on", ("codebase", "memory on")),
    ("/thinking ", "high", ("thinking", "high")),
    ("/model gp", "gpt-6.1-sol", ("model", "gpt-6.1-sol")),
])
def test_final_argument_selection_executes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, choice: str, expected: tuple[str, str],
) -> None:
    calls = []
    monkeypatch.setattr(MTPApp, "_dispatch_command", lambda self, cmd, arg: calls.append((cmd, arg)))

    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            input_area = app.query_one(InputArea)
            input_area.text = text
            input_area.cursor_location = (0, len(text))
            await pilot.pause()
            options = app.query_one("#suggestion-list", OptionList)
            # Drive the same OptionSelected message used by keyboard and click.
            if text.endswith(choice + " "):
                assert not options.has_class("visible")
                input_area.focus()
            else:
                options.highlighted = next(i for i, option in enumerate(options.options) if str(option.prompt) == "-> " + choice)
                options.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert calls == [expected]
            assert input_area.text == ""
            assert not options.has_class("visible")
            assert app.focused is input_area

    asyncio.run(scenario())


def test_slash_picker_advances_then_executes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(MTPApp, "_dispatch_command", lambda self, cmd, arg: calls.append((cmd, arg)))

    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            input_area = app.query_one(InputArea)
            input_area.text = "/codeb"
            input_area.cursor_location = (0, 6)
            await pilot.pause()
            options = app.query_one("#suggestion-list", OptionList)
            options.highlighted = 0
            options.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert input_area.text == "/codebase "
            options.highlighted = 0  # memory
            options.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert input_area.text == "/codebase memory "
            assert calls == []
            options.highlighted = 0  # on
            options.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert calls == [("codebase", "memory on")]

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["enter", "tab"])
def test_first_completion_key_selects_without_extra_press(tmp_path, monkeypatch, key) -> None:
    calls = []
    monkeypatch.setattr(MTPApp, "_dispatch_command", lambda self, cmd, arg: calls.append((cmd, arg)))

    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            input_area = app.query_one(InputArea)
            input_area.text = "/backend gr"
            input_area.cursor_location = (0, len(input_area.text))
            input_area.focus()
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            assert calls == [("backend", "groq")]

    asyncio.run(scenario())


def test_sessions_argument_opens_selected_session(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(MTPApp, "_dispatch_command", lambda self, cmd, arg: calls.append((cmd, arg)))

    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            input_area = app.query_one(InputArea)
            input_area.text = "/sessions "
            input_area.cursor_location = (0, len(input_area.text))
            await pilot.pause()
            app._populate_and_show_suggestions(["saved001"], prefix="-> ")
            options = app.query_one("#suggestion-list", OptionList)
            options.highlighted = 0
            options.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert calls == [("load", "saved001")]
            assert input_area.text == ""

    asyncio.run(scenario())
