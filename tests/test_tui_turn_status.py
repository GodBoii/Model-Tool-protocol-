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

from test_tui_app_lifecycle import FakeRunner, _make_state  # noqa: E402


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
