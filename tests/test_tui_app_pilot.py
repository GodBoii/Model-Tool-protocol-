from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, RichLog

from mtp import JsonSessionStore
from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_state import TUIState
from mtp.cli.tui_widgets.boot_screen import BootScreen
from mtp.cli.tui_widgets.chat_log import AssistantMessageWidget, ChatLog
from mtp.cli.tui_widgets.input_area import InputArea
from mtp.cli.tui_widgets.api_key_dialog import APIKeyDialog
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


@pytest.mark.asyncio
async def test_inline_apikey_is_scrubbed_from_history_undo_transcript_and_rendering(
    pilot_app: MTPApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtp.cli import tui_settings

    secret = "gsk-secret-retained-1234"
    stored: dict[tuple[str, str], str] = {}

    class MemoryKeyring:
        def get_password(self, service: str, username: str) -> str | None:
            return stored.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            stored[(service, username)] = password

    monkeypatch.setattr(tui_settings, "_keyring", lambda: MemoryKeyring())
    async with pilot_app.run_test(size=(100, 32)) as pilot:
        await pilot.pause(0.6)
        input_area = pilot_app.query_one(InputArea)
        input_area.text = f"/apikey set groq {secret}"
        await pilot.press("enter")
        await pilot.pause()

        assert stored[(tui_settings.KEYRING_SERVICE, "groq")] == secret
        assert pilot_app._input_history == []
        assert pilot_app.state.transcript == []
        assert input_area.text == ""
        assert input_area.history.undo_stack == []
        assert input_area.history.redo_stack == []

        input_area.action_undo()
        pilot_app.on_input_area_history_navigate(InputArea.HistoryNavigate(-1))
        await pilot.pause()
        assert secret not in input_area.text
        rendered = pilot_app.export_screenshot()
        assert secret not in rendered
        assert secret[:4] not in rendered
        assert secret[-4:] not in rendered


@pytest.mark.asyncio
async def test_apikey_modal_masks_and_scrubs_secret_input(
    pilot_app: MTPApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtp.cli import tui_settings

    secret = "sk-modal-secret-5678"
    stored: dict[tuple[str, str], str] = {}

    class MemoryKeyring:
        def get_password(self, service: str, username: str) -> str | None:
            return stored.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            stored[(service, username)] = password

    monkeypatch.setattr(tui_settings, "_keyring", lambda: MemoryKeyring())
    async with pilot_app.run_test(size=(100, 32)) as pilot:
        await pilot.pause(0.6)
        pilot_app._dispatch_command("apikey", "set openai")
        await pilot.pause()

        dialog = pilot_app.screen
        assert isinstance(dialog, APIKeyDialog)
        secret_input = dialog.query_one("#api-key-input", Input)
        assert secret_input.password is True
        secret_input.value = secret
        await pilot.press("enter")
        await pilot.pause()

        assert stored[(tui_settings.KEYRING_SERVICE, "openai")] == secret
        assert secret_input.value == ""
        assert pilot_app._input_history == []
        assert pilot_app.state.transcript == []
        rendered = pilot_app.export_screenshot()
        assert secret not in rendered
        assert secret[:4] not in rendered
        assert secret[-4:] not in rendered
