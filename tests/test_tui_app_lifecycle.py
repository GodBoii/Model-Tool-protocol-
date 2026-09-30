"""Pilot tests for the Textual TUI run lifecycle.

The real LLM call is replaced with a fake ``run_prompt_blocking`` that the test
drives step by step, so these tests exercise the app's event routing, worker
handling and rendering without any provider or network.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("textual")

from mtp import JsonSessionStore
from mtp.cli import tui_app
from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_state import ChatResult, TranscriptTurn, TUIState, now_label
from mtp.cli.tui_widgets.chat_log import ChatLog, UserMessageWidget
from mtp.cli.tui_workers import save_tui_session


class FakeRunner:
    """Stands in for ``run_prompt_blocking``; each call blocks until released."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._started = threading.Semaphore(0)

    def __call__(
        self,
        state: TUIState,
        prompt: str,
        *,
        emit_callback: Callable[[str, Any], None],
        run_id: str | None,
        codex_handle: Any = None,
    ) -> ChatResult:
        release = threading.Event()
        self.calls.append({
            "prompt": prompt, "emit": emit_callback, "run_id": run_id,
            "release": release, "codex_handle": codex_handle,
        })
        self._started.release()
        release.wait(timeout=10)
        return ChatResult(text=f"answer to {prompt}", tool_events=[], attachments=[], warnings=[], usage_lines=[])

    async def wait_started(self) -> dict[str, Any]:
        acquired = await asyncio.to_thread(self._started.acquire, True, 5)
        assert acquired, "fake run never started"
        return self.calls[-1]


def _make_state(tmp_path: Path) -> TUIState:
    return TUIState(
        backend="codex",
        codex_model="gpt-test",
        openai_model="gpt-test",
        max_rounds=2,
        cwd=tmp_path,
        autoresearch=False,
        research_instructions=None,
        reasoning_effort="medium",
        harness_mode="code",
        codex_sandbox_mode="workspace-write",
        last_usage_lines=[],
        transcript=[],
        session_store=JsonSessionStore(db_path=tmp_path / "sessions"),
        session_id="chat-test000001",
        session_label=None,
        user_id="tui-user",
    )


def _live_text(app: MTPApp) -> str:
    live = app.query_one("#chat-log", ChatLog)._live_widget
    if live is None:
        return ""
    return "".join(
        str(block.get("text") or "") for block in live._msg.assistant_blocks if block.get("type") == "text"
    )


async def _emit_from_thread(emit: Callable[[str, Any], None], kind: str, message: Any) -> None:
    # emit_live uses call_from_thread, so it must be called off the event loop.
    await asyncio.to_thread(emit, kind, message)


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    runner = FakeRunner()
    monkeypatch.setattr(tui_app, "run_prompt_blocking", runner)
    return runner


def test_throttled_live_events_are_flushed(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered: list[str] = []
    original = ChatLog.set_live_assistant_message

    def recording(self: ChatLog, msg: Any) -> None:
        # Snapshot at render time; block dicts are mutated in place afterwards.
        rendered.append(
            "".join(str(b.get("text") or "") for b in msg.assistant_blocks if b.get("type") == "text")
        )
        original(self, msg)

    monkeypatch.setattr(ChatLog, "set_live_assistant_message", recording)

    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("hi")
            run = await fake_runner.wait_started()
            emit = run["emit"]

            def burst() -> None:
                # Non-text events flush immediately, so these arrive as several
                # events inside one throttle window.
                emit("text", "Hello")
                emit("warn", "w1")
                emit("text", " world")
                emit("warn", "w2")

            await asyncio.to_thread(burst)
            await pilot.pause(0.3)
            assert rendered and rendered[-1] == "Hello world"
            run["release"].set()
            await pilot.pause(0.2)

    asyncio.run(scenario())


def test_events_from_a_finished_run_are_ignored(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("first")
            first = await fake_runner.wait_started()
            first["release"].set()
            await pilot.pause(0.3)
            assert len(app.state.transcript) == 1

            app._send_prompt("second")
            second = await fake_runner.wait_started()
            await _emit_from_thread(first["emit"], "text", "STALE")
            await _emit_from_thread(second["emit"], "text", "fresh")
            await pilot.pause(0.3)
            assert _live_text(app) == "fresh"
            second["release"].set()
            await pilot.pause(0.3)
            assert [t.response for t in app.state.transcript] == ["answer to first", "answer to second"]

    asyncio.run(scenario())


def test_memory_scan_does_not_cancel_llm_run(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_done = threading.Event()

    async def fake_scan(self: MTPApp, root: Path) -> Any:
        await asyncio.to_thread(scan_done.wait, 5)
        return tui_app.CodebaseScanResult(
            root=root, files_indexed=0, changed_files=0, files_deleted=0, chunks_indexed=0, db_path=root / "db"
        )

    monkeypatch.setattr(MTPApp, "_run_codebase_scan_worker", fake_scan)

    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("hi")
            run = await fake_runner.wait_started()
            app._start_codebase_scan(tmp_path, app.query_one("#chat-log", ChatLog))
            await pilot.pause(0.1)
            assert app._llm_worker is not None and not app._llm_worker.is_cancelled
            scan_done.set()
            run["release"].set()
            await pilot.pause(0.3)
            assert [t.response for t in app.state.transcript] == ["answer to hi"]

    asyncio.run(scenario())


def test_load_command_renders_loaded_transcript(tmp_path: Path, fake_runner: FakeRunner) -> None:
    saved = _make_state(tmp_path)
    saved.session_id = "chat-saved00001"
    saved.transcript = [
        TranscriptTurn(
            prompt="saved question", response="saved answer", backend="codex", model="gpt-test",
            attachments=[], warnings=[], usage_lines=[], created_at=now_label(),
        )
    ]
    save_tui_session(saved)

    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            chat_log = app.query_one("#chat-log", ChatLog)
            assert not list(chat_log.query(UserMessageWidget))
            app._dispatch_command("load", "chat-saved00001")
            await pilot.pause(0.2)
            assert app.state.session_id == "chat-saved00001"
            assert len(list(chat_log.query(UserMessageWidget))) == 1

    asyncio.run(scenario())


def test_escape_cancels_codex_run(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._send_prompt("hi")
            run = await fake_runner.wait_started()
            handle = run["codex_handle"]
            assert handle is not None and not handle.cancelled
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert handle.cancelled
            run["release"].set()
            await pilot.pause(0.3)

    asyncio.run(scenario())


def test_completed_turn_is_saved_off_the_ui_thread(tmp_path: Path, fake_runner: FakeRunner) -> None:
    state = _make_state(tmp_path)
    ui_thread_writes: list[str] = []
    real_upsert = state.session_store.upsert_session

    def tracking_upsert(record: Any) -> Any:
        ui_thread_writes.append(threading.current_thread().name)
        return real_upsert(record)

    state.session_store.upsert_session = tracking_upsert  # type: ignore[method-assign]

    async def scenario() -> None:
        app = MTPApp(state=state)
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("remember me")
            run = await fake_runner.wait_started()
            run["release"].set()
            await pilot.pause(0.3)
            await asyncio.to_thread(app.session_saver.flush)
            stored = state.session_store.get_session(session_id=state.session_id, user_id=state.user_id)
            assert stored is not None
            assert stored.metadata["tui"]["turn_count"] == 1
            assert stored.metadata["tui"]["session_label"]

    asyncio.run(scenario())
    assert ui_thread_writes, "session was never written"
    assert all(name.startswith("mtp-session-save") for name in ui_thread_writes)


def test_pending_save_is_flushed_on_exit(tmp_path: Path, fake_runner: FakeRunner) -> None:
    state = _make_state(tmp_path)

    async def scenario() -> None:
        app = MTPApp(state=state)
        async with app.run_test(size=(120, 40)) as pilot:
            app._dispatch_command("rounds", "9")
            await pilot.pause()

    asyncio.run(scenario())
    stored = state.session_store.get_session(session_id=state.session_id, user_id=state.user_id)
    assert stored is not None
    assert stored.metadata["tui"]["max_rounds"] == 9


def test_file_suggestions_come_from_background_index(tmp_path: Path, fake_runner: FakeRunner) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget_target.py").write_text("x", encoding="utf-8")

    async def scenario() -> None:
        from textual.widgets import OptionList

        from mtp.cli.tui_widgets.input_area import InputArea

        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            input_area = app.query_one("#chat-input", InputArea)
            input_area.focus()
            input_area.insert("look at @widget_t")
            await pilot.pause(0.3)
            options = app.query_one("#suggestion-list", OptionList)
            assert options.has_class("visible")
            labels = [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]
            assert labels == ["@src/widget_target.py"]

    asyncio.run(scenario())


def test_backend_switch_runs_off_the_ui_thread(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtp.cli.tui_workers import BackendSwitch

    threads: list[str] = []
    delays = {"slow": 0.4, "fast": 0.0}

    def fake_prepare(state: TUIState, name: str) -> BackendSwitch:
        threads.append(threading.current_thread().name)
        import time as _time
        _time.sleep(delays[name])
        return BackendSwitch(f"switched to {name}", backend=name)

    monkeypatch.setattr(tui_app, "prepare_backend_switch", fake_prepare)

    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            app._dispatch_command("backend", "slow")
            assert app.state.backend == "codex"  # not applied synchronously
            app._dispatch_command("backend", "fast")
            await pilot.pause(0.7)
            # The newer request wins even though the older one finished last.
            assert app.state.backend == "fast"

    asyncio.run(scenario())
    assert len(threads) == 2
    assert threading.main_thread().name not in threads


def test_input_is_focused_on_first_frame(tmp_path: Path, fake_runner: FakeRunner) -> None:
    async def scenario() -> None:
        from mtp.cli.tui_widgets.input_area import InputArea

        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.focused, InputArea)

    asyncio.run(scenario())
