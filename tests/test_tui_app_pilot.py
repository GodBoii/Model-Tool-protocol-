from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import OptionList, RichLog

from mtp import JsonSessionStore
from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_state import TUIState
from mtp.cli.tui_widgets.boot_screen import BootScreen
from mtp.cli.tui_widgets.chat_log import AssistantMessageWidget, ChatLog
from mtp.cli.tui_widgets.input_area import InputArea
from mtp.cli.tui_widgets.sidebar import Sidebar
from mtp.cli.tui_widgets.status_bar import StatusBar


def _state(tmp_path: Path) -> TUIState:
    return TUIState(
        backend="codex",
        codex_model="test-model",
        openai_model="gpt-test",
        max_rounds=3,
        cwd=tmp_path,
        autoresearch=False,
        research_instructions=None,
        reasoning_effort="none",
        harness_mode="balanced",
        codex_sandbox_mode="read-only",
        last_usage_lines=[],
        transcript=[],
        session_store=JsonSessionStore(db_path=tmp_path / "sessions"),
        session_id="chat-pilot1234",
        session_label=None,
        user_id=None,
    )


@pytest.fixture
def pilot_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MTPApp:
    # A pilot test must never start an index scan or depend on host workspace state.
    monkeypatch.setattr(MTPApp, "_request_background_memory_refresh", lambda *args, **kwargs: None)
    return MTPApp(_state(tmp_path))


@pytest.mark.asyncio
async def test_pilot_startup_mounts_complete_ui_and_focuses_input(pilot_app: MTPApp) -> None:
    async with pilot_app.run_test(size=(100, 32)) as pilot:
        await pilot.pause(0.6)

        assert pilot_app.query_one(BootScreen).display
        assert pilot_app.query_one(InputArea).has_focus
        assert pilot_app.query_one(ChatLog)
        assert pilot_app.query_one(StatusBar)
        assert not pilot_app.query_one(Sidebar).has_class("visible")


@pytest.mark.asyncio
async def test_pilot_command_typing_submission_and_history(pilot_app: MTPApp) -> None:
    async with pilot_app.run_test(size=(100, 32)) as pilot:
        await pilot.pause(0.6)
        await pilot.press("slash", "s", "t", "a", "t", "u", "s")
        await pilot.press("tab")
        await pilot.pause()
        assert pilot_app.query_one(OptionList).has_class("visible")

        await pilot.press("escape", "enter")
        await pilot.pause()
        assert pilot_app.query_one(RichLog).has_class("visible")
        assert pilot_app._input_history == ["/status"]

        pilot_app.query_one(InputArea).focus()
        await pilot.press("up")
        await pilot.pause()
        assert pilot_app.query_one(InputArea).text == "/status"


@pytest.mark.asyncio
async def test_pilot_narrow_terminal_keeps_input_and_status_on_screen(pilot_app: MTPApp) -> None:
    async with pilot_app.run_test(size=(40, 16)) as pilot:
        await pilot.pause(0.6)
        input_area = pilot_app.query_one(InputArea)
        status = pilot_app.query_one(StatusBar)

        assert input_area.region.width > 0
        assert input_area.region.height > 0
        assert input_area.region.bottom <= pilot_app.screen.region.bottom
        assert status.region.bottom <= pilot_app.screen.region.bottom

        await pilot.press("ctrl+b")
        await pilot.pause()
        assert pilot_app.query_one(Sidebar).has_class("visible")
        assert pilot_app.query_one("#main-container").region.width > 0


@pytest.mark.asyncio
async def test_pilot_streaming_events_update_one_live_widget_in_place(pilot_app: MTPApp) -> None:
    async with pilot_app.run_test(size=(100, 32)) as pilot:
        pilot_app.query_one(BootScreen).display = False
        pilot_app._pending_display_prompt = "stream this"
        pilot_app._rebuild_chat_log()

        pilot_app._handle_live_event("reasoning", "checking")
        pilot_app._handle_live_event("text", "hello")
        await pilot.pause(0.12)
        assistant = pilot_app.query_one(AssistantMessageWidget)

        pilot_app._handle_live_event("text", " world")
        pilot_app._render_live_preview(force=True)
        await pilot.pause()

        assert pilot_app.query_one(AssistantMessageWidget) is assistant
        assert pilot_app._live_thinking_text == "checking"
        assert pilot_app._live_blocks[-1] == {"type": "text", "text": "hello world"}
