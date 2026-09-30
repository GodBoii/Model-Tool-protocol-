"""Several chats running at once, queued messages, steering and interrupts."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("textual")

from mtp.cli import tui_app
from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_state import TURN_CANCELLED, ChatResult
from mtp.cli.tui_widgets.chat_log import UserMessageWidget
from mtp.cli.tui_widgets.input_area import InputArea

from test_tui_app_lifecycle import FakeRunner, _make_state, fake_runner  # noqa: F401  (fixture)


def _live_text(chat_log: Any) -> str:
    live = chat_log._live_widget
    if live is None:
        return ""
    return "".join(str(b.get("text") or "") for b in live._msg.assistant_blocks if b.get("type") == "text")


class _SteerableAgent:
    """Stands in for MTPAgent; records steering calls."""

    def __init__(self) -> None:
        self.steered: list[tuple[str, str]] = []

    def steer_run(self, run_id: str, text: str) -> bool:
        self.steered.append((run_id, text))
        return True

    def cancel_run(self, run_id: str) -> bool:
        return True


def test_two_chats_stream_in_parallel(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            first = app.active_conversation
            app._send_prompt("task one")
            run_one = await fake_runner.wait_started()

            app.action_new_conversation()
            await pilot.pause()
            second = app.active_conversation
            assert second is not first
            app._send_prompt("task two")
            run_two = await fake_runner.wait_started()
            assert first.running and second.running

            # The chat in the background keeps streaming into its own log.
            await asyncio.to_thread(run_one["emit"], "text", "progress on one")
            await asyncio.to_thread(run_two["emit"], "text", "progress on two")
            await pilot.pause(0.3)
            assert _live_text(first.view.chat_log) == "progress on one"
            assert _live_text(second.view.chat_log) == "progress on two"

            run_one["release"].set()
            await pilot.pause(0.3)
            assert not first.running and second.running
            assert first.unread
            assert [t.response for t in first.state.transcript] == ["answer to task one"]
            assert "●" in str(app._tab_label(first))

            app.action_switch_conversation(1)
            await pilot.pause()
            assert app.active_conversation is first and not first.unread

            run_two["release"].set()
            await pilot.pause(0.3)
            assert [t.response for t in second.state.transcript] == ["answer to task two"]
            assert first.state.session_id != second.state.session_id

    asyncio.run(scenario())


def test_message_sent_during_a_run_is_queued_then_runs(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            conv = app.active_conversation
            app._send_prompt("first")
            run = await fake_runner.wait_started()
            app._send_prompt("follow-up")
            await pilot.pause()
            assert [q.text for q in conv.queue] == ["follow-up"]
            assert len(fake_runner.calls) == 1
            assert app.query_one("#queue-bar").has_class("visible")

            run["release"].set()
            follow = await fake_runner.wait_started()
            assert follow["prompt"] == "follow-up"
            assert not conv.queue
            follow["release"].set()
            await pilot.pause(0.3)
            assert [t.prompt for t in conv.state.transcript] == ["first", "follow-up"]
            assert not app.query_one("#queue-bar").has_class("visible")

    asyncio.run(scenario())


def test_ctrl_g_steers_queued_message_into_the_run(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        state = _make_state(tmp_path)
        state.backend = "groq"
        agent = _SteerableAgent()
        state.agent = agent
        app = MTPApp(state=state)
        async with app.run_test(size=(120, 40)) as pilot:
            conv = app.active_conversation
            app._send_prompt("build it")
            run = await fake_runner.wait_started()
            app._send_prompt("use the v2 api")
            await pilot.pause()
            assert len(conv.queue) == 1

            await pilot.press("ctrl+g")
            await pilot.pause(0.2)
            assert agent.steered == [(run["run_id"], "use the v2 api")]
            assert not conv.queue
            assert any(b.get("type") == "steer" for b in conv.live.blocks)

            run["release"].set()
            await pilot.pause(0.3)
            [turn] = conv.state.transcript
            assert {"type": "steer", "text": "use the v2 api"} in turn.assistant_blocks
            assert len(fake_runner.calls) == 1  # steered, not re-run

    asyncio.run(scenario())


def test_codex_chat_only_queues(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))  # codex backend
        async with app.run_test(size=(120, 40)) as pilot:
            conv = app.active_conversation
            app._send_prompt("go")
            run = await fake_runner.wait_started()
            app._send_prompt("more")
            await pilot.pause()
            assert not app._can_steer(conv)
            await pilot.press("ctrl+g")
            await pilot.pause()
            assert [q.text for q in conv.queue] == ["more"]
            run["release"].set()
            nxt = await fake_runner.wait_started()
            nxt["release"].set()
            await pilot.pause(0.3)

    asyncio.run(scenario())


def test_late_steering_runs_as_the_next_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def runner(state: Any, prompt: str, **_: Any) -> ChatResult:
        prompts.append(prompt)
        late = ["late note"] if len(prompts) == 1 else []
        return ChatResult(text=f"reply {len(prompts)}", tool_events=[], attachments=[], warnings=[],
                          usage_lines=[], unapplied_steering=late)

    monkeypatch.setattr(tui_app, "run_prompt_blocking", runner)

    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("original")
            await pilot.pause(0.6)
            assert prompts == ["original", "late note"]

    asyncio.run(scenario())


def test_ctrl_x_stops_the_run_but_cuts_when_idle(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            input_area = app.query_one("#chat-input", InputArea)
            input_area.focus()
            input_area.insert("draft text")
            input_area.select_all()
            await pilot.press("ctrl+x")
            await pilot.pause()
            assert input_area.text == ""  # idle: Ctrl+X is cut

            app._send_prompt("long job")
            run = await fake_runner.wait_started()
            handle = run["codex_handle"]
            input_area.insert("keep me")
            input_area.select_all()
            await pilot.press("ctrl+x")
            await pilot.pause()
            assert handle.cancelled
            assert input_area.text == "keep me"  # running: Ctrl+X stops the run
            run["release"].set()
            await pilot.pause(0.3)
            assert app.active_conversation.state.transcript[-1].status == TURN_CANCELLED

    asyncio.run(scenario())


def test_escape_closes_suggestions_before_interrupting(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("job")
            run = await fake_runner.wait_started()
            handle = run["codex_handle"]
            app._show_command_suggestions("/")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not handle.cancelled
            assert not app.query_one("#suggestion-list").has_class("visible")
            await pilot.press("escape")
            await pilot.pause()
            assert handle.cancelled
            run["release"].set()
            await pilot.pause(0.3)

    asyncio.run(scenario())


def test_close_rules(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            assert "only open chat" in app._close_conversation(app.active_conversation)
            app.action_new_conversation()
            await pilot.pause()
            second = app.active_conversation
            app._send_prompt("busy")
            run = await fake_runner.wait_started()
            assert "still running" in app._close_conversation(second)
            run["release"].set()
            await pilot.pause(0.3)
            assert "Closed" in app._close_conversation(second)
            await pilot.pause()
            assert len(app.conversations) == 1
            assert not list(app.query(f"#{second.view.id}"))

    asyncio.run(scenario())


def test_switching_backend_mid_run_labels_the_turn_with_the_original(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtp.cli.tui_workers import BackendSwitch

    monkeypatch.setattr(tui_app, "prepare_backend_switch", lambda state, name: BackendSwitch("ok", backend=name))

    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            conv = app.active_conversation
            app._send_prompt("q")
            run = await fake_runner.wait_started()
            app._dispatch_command("backend", "groq")
            await pilot.pause(0.2)
            assert conv.state.backend == "groq"
            run["release"].set()
            await pilot.pause(0.3)
            assert conv.state.transcript[-1].backend == "codex"

    asyncio.run(scenario())


def test_new_chat_starts_empty_and_keeps_settings(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        state = _make_state(tmp_path)
        state.harness_mode = "review"
        app = MTPApp(state=state)
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("hello")
            run = await fake_runner.wait_started()
            run["release"].set()
            await pilot.pause(0.3)
            await pilot.press("ctrl+n")
            await pilot.pause()
            new = app.active_conversation
            assert new.state.transcript == []
            assert new.state.harness_mode == "review"
            assert not list(new.view.chat_log.query(UserMessageWidget))
            await pilot.press("alt+1")
            await pilot.pause()
            assert app.active_conversation is app.conversations[0]

    asyncio.run(scenario())


def test_every_documented_shortcut_has_a_binding() -> None:
    from mtp.cli.tui_shortcuts import SHORTCUTS

    bound: set[str] = set()
    for binding in MTPApp.BINDINGS:
        for key in binding.key.split(","):
            bound.add(key.strip())
    # Keys handled by the input widget itself rather than app bindings.
    handled_by_input = {"Enter", "Shift+Enter"}
    expand = {"F1..F9": ["f1", "f9"], "Alt+Left/Right": ["alt+left", "alt+right"], "Esc": ["escape"]}
    for shortcut in SHORTCUTS:
        if shortcut.keys in handled_by_input:
            continue
        keys = expand.get(shortcut.keys, [shortcut.keys.lower()])
        for key in keys:
            assert key in bound, f"{shortcut.keys} is documented but not bound"


def test_chat_keeps_most_of_the_screen_while_running_with_a_queue(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(110, 34)) as pilot:
            app._send_prompt("hello")
            run = await fake_runner.wait_started()
            app._send_prompt("queued")
            await pilot.pause(0.3)
            assert app.query_one("#conversation-tabs").region.height == 2
            assert app.active_chat_log.region.height >= 15
            queue_bar = app.query_one("#queue-bar")
            input_panel = app.query_one("#input-panel")
            # The queue sits inside the docked input panel, above the prompt.
            assert input_panel.region.contains_region(queue_bar.region)
            run["release"].set()
            nxt = await fake_runner.wait_started()
            nxt["release"].set()
            await pilot.pause(0.3)

    asyncio.run(scenario())
