"""Failed and cancelled turns keep their output and the conversation continues."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterator

import pytest

from mtp.cli.tui_mtp_backend import run_mtp_prompt
from mtp.cli.tui_state import (
    TURN_CANCELLED, TURN_COMPLETED, TURN_FAILED,
    ChatResult, TranscriptTurn, deserialize_transcript, now_label, serialize_transcript,
)


class _EventsAgent:
    def __init__(self, events: list[dict[str, Any]], *, fail_with: Exception | None = None) -> None:
        self._events = events
        self._fail_with = fail_with

    def run_events(self, **_: Any) -> Iterator[dict[str, Any]]:
        yield from self._events
        if self._fail_with is not None:
            raise self._fail_with


def test_provider_error_keeps_partial_output() -> None:
    agent = _EventsAgent(
        [{"type": "text_chunk", "chunk": "Half an ", "source": "direct"},
         {"type": "text_chunk", "chunk": "answer", "source": "direct"}],
        fail_with=ConnectionError("socket closed"),
    )
    result = run_mtp_prompt(agent=agent, prompt="x", max_rounds=1)
    assert result.status == TURN_FAILED
    assert result.error == "ConnectionError: socket closed"
    assert result.text == "Half an answer"
    assert [b["text"] for b in result.assistant_blocks if b["type"] == "text"] == ["Half an answer"]


def test_run_cancelled_event_marks_turn_cancelled() -> None:
    agent = _EventsAgent([
        {"type": "text_chunk", "chunk": "Working on", "source": "direct"},
        {"type": "run_cancelled", "round": 1},
    ])
    result = run_mtp_prompt(agent=agent, prompt="x", max_rounds=1)
    assert result.status == TURN_CANCELLED
    assert result.error is None
    assert result.text == "Working on"


def test_status_round_trips_and_old_records_default_to_completed() -> None:
    turn = TranscriptTurn(
        prompt="p", response="partial", backend="codex", model="m", attachments=[], warnings=[],
        usage_lines=[], created_at=now_label(), status=TURN_FAILED, error="Boom",
    )
    [restored] = deserialize_transcript(serialize_transcript([turn]))
    assert (restored.status, restored.error) == (TURN_FAILED, "Boom")
    [legacy] = deserialize_transcript([{"prompt": "p", "response": "r"}])
    assert (legacy.status, legacy.error) == (TURN_COMPLETED, None)


def test_history_reply_tells_the_model_how_the_turn_ended() -> None:
    base = dict(prompt="p", backend="codex", model="m", attachments=[], warnings=[], usage_lines=[], created_at="t")
    assert TranscriptTurn(response="ok", **base).history_reply() == "ok"
    cancelled = TranscriptTurn(response="half", status=TURN_CANCELLED, **base).history_reply()
    assert cancelled.startswith("half") and "interrupted by the user" in cancelled
    failed = TranscriptTurn(response="", status=TURN_FAILED, error="Timeout", **base).history_reply()
    assert "(no output)" in failed and "failed: Timeout" in failed


pytest.importorskip("textual")

from mtp.cli import tui_app  # noqa: E402
from mtp.cli.tui_app import MTPApp  # noqa: E402

from test_tui_app_lifecycle import _make_state  # noqa: E402


def _status_texts(app: MTPApp) -> list[str]:
    return [str(w.render()) for w in app.query(".assistant-status")]


def test_failed_turn_is_saved_and_conversation_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter([
        ChatResult(text="partial", tool_events=[], attachments=[], warnings=[], usage_lines=[],
                   status=TURN_FAILED, error="RateLimitError: slow down"),
        ChatResult(text="second answer", tool_events=[], attachments=[], warnings=[], usage_lines=[]),
    ])
    monkeypatch.setattr(tui_app, "run_prompt_blocking", lambda *a, **k: next(results))

    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("first")
            await pilot.pause(0.4)
            [turn] = app.state.transcript
            assert (turn.status, turn.response, turn.error) == (TURN_FAILED, "partial", "RateLimitError: slow down")
            assert any("Run failed" in text and "slow down" in text for text in _status_texts(app))

            app._send_prompt("try again")
            await pilot.pause(0.4)
            assert [t.status for t in app.state.transcript] == [TURN_FAILED, TURN_COMPLETED]

    asyncio.run(scenario())


def test_crashing_worker_saves_live_output_as_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(state: Any, prompt: str, *, emit_callback: Any, run_id: Any, codex_handle: Any = None) -> ChatResult:
        emit_callback("text", "streamed before the crash")
        raise RuntimeError("worker blew up")

    monkeypatch.setattr(tui_app, "run_prompt_blocking", crash)

    async def scenario() -> None:
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("go")
            await pilot.pause(0.5)
            [turn] = app.state.transcript
            assert turn.status == TURN_FAILED
            assert turn.response == "streamed before the crash"
            assert turn.error == "RuntimeError: worker blew up"

    asyncio.run(scenario())


def test_crash_drains_emitted_output_before_delayed_live_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from textual.worker import WorkerFailed
    from tui_test_helpers import wait_until
    from mtp.cli.tui_widgets.stream_markdown import MarkdownBlock, StreamingMarkdown

    class DelayedLiveApp(MTPApp):
        CSS_PATH = Path(tui_app.__file__).with_name("tui_app.tcss")
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.delayed = []
            self.saved_turn_counts = []

        def post_message(self, message):
            if isinstance(message, tui_app.LiveEventBatch):
                self.delayed.append(message)
                return True
            return super().post_message(message)

        def _save_session(self, conv=None):
            owner = conv or self._active
            if owner.state.transcript:
                self.saved_turn_counts.append(len(owner.state.transcript))
            super()._save_session(conv)

    emitters = []
    def crash(state, prompt, *, emit_callback, **kwargs):
        emitters.append(emit_callback)
        if prompt == "again":
            emit_callback("text", "next answer")
            return ChatResult(text="next answer", tool_events=[], attachments=[], warnings=[], usage_lines=[])
        emit_callback("text", "streamed before ")
        emit_callback("reasoning", "checking")
        emit_callback("text", "the crash")
        emit_callback("warn", "warning before crash")
        raise RuntimeError("worker blew up")
    monkeypatch.setattr(tui_app, "run_prompt_blocking", crash)

    async def scenario():
        state = _make_state(tmp_path)
        app = DelayedLiveApp(state=state)
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("go")
            worker = app._active.worker
            with pytest.raises(WorkerFailed):
                await worker.wait()
            await wait_until(pilot, lambda: len(state.transcript) == 1)
            [turn] = state.transcript
            assert (turn.status, turn.response, turn.error) == (TURN_FAILED, "streamed before the crash", "RuntimeError: worker blew up")
            assert turn.thinking_text == "checking"
            assert turn.warnings == ["warning before crash"]
            await wait_until(pilot, lambda: any("Run failed" in text for text in _status_texts(app)))
            assert any("Run failed" in text for text in _status_texts(app))
            visible = list(app.query(MarkdownBlock)) + list(app.query(StreamingMarkdown))
            assert "streamed before the crash" in "".join(widget.text for widget in visible)
            assert "streamed before the crash" in turn.history_reply()
            await asyncio.to_thread(app.session_saver.flush)
            saved = state.session_store.get_session(state.session_id, user_id=state.user_id)
            [persisted] = saved.metadata["tui"]["transcript"]
            assert persisted["response"] == "streamed before the crash" and persisted["status"] == TURN_FAILED
            assert app.saved_turn_counts == [1]
            assert not app._pending_live_events

            # Queued notifications and a cancelled thread's late callback cannot
            # duplicate the failed turn or enter the next run's conversation.
            for message in app.delayed:
                app.on_live_event_batch(message)
            await asyncio.to_thread(emitters[0], "text", " late leaked output")
            assert state.transcript == [turn]
            assert turn.response == "streamed before the crash"
            assert app.saved_turn_counts == [1]
            app._send_prompt("again")
            await asyncio.to_thread(emitters[0], "text", " output for the wrong turn")
            await app._active.worker.wait()
            await wait_until(pilot, lambda: len(state.transcript) == 2)
            assert [item.response for item in state.transcript] == ["streamed before the crash", "next answer"]
            assert app.saved_turn_counts == [1, 2]
    asyncio.run(scenario())


def test_cancellation_drains_tokens_before_batcher_notifies_ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    from textual.worker import WorkerCancelled
    from tui_test_helpers import wait_until

    started = threading.Event()
    release = threading.Event()
    callbacks = []
    delayed = []
    original = MTPApp.post_message
    def postpone(self, message):
        if isinstance(message, tui_app.LiveEventBatch):
            delayed.append(message)
            return True
        return original(self, message)
    def blocking(state, prompt, *, emit_callback, **kwargs):
        callbacks.append(emit_callback)
        emit_callback("text", "partial before cancellation")
        started.set()
        release.wait(5)
        emit_callback("text", "late after cancellation")
        return ChatResult(text="late result", tool_events=[], attachments=[], warnings=[], usage_lines=[])
    monkeypatch.setattr(MTPApp, "post_message", postpone)
    monkeypatch.setattr(tui_app, "run_prompt_blocking", blocking)

    async def scenario():
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            app._send_prompt("go")
            assert await asyncio.to_thread(started.wait, 5)
            worker = app._active.worker
            worker.cancel()
            with pytest.raises(WorkerCancelled):
                await worker.wait()
            await wait_until(pilot, lambda: len(app.state.transcript) == 1)
            [turn] = app.state.transcript
            assert (turn.status, turn.response) == (TURN_CANCELLED, "partial before cancellation")
            await asyncio.to_thread(callbacks[0], "text", " stale output")
            release.set()
            for message in delayed:
                app.on_live_event_batch(message)
            assert turn.response == "partial before cancellation"
            assert not app._pending_live_events
    try:
        asyncio.run(scenario())
    finally:
        release.set()
