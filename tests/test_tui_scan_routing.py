"""Codebase scans belong to their initiating chat, even after navigation."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mtp.cli.tui_app import CodebaseScanResult, MTPApp
from test_tui_app_lifecycle import _make_state


@pytest.mark.parametrize("change", ["switch", "close", "cd"])
def test_scan_completion_respects_its_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    async def scenario() -> None:
        finished = asyncio.Event()

        async def scan(self, root):
            await finished.wait()
            return CodebaseScanResult(root, 1, 1, 0, 1, root / "db")

        monkeypatch.setattr(MTPApp, "_run_codebase_scan_worker", scan)
        monkeypatch.setattr(MTPApp, "_request_background_memory_refresh", lambda *args, **kwargs: None)
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            first = app.active_conversation
            original_agent = object()
            first.state.agent = original_agent
            root = tmp_path / "scan-root"
            app._start_codebase_scan(root, app.active_chat_log)
            worker = app._codebase_scan_context[0]
            app.action_new_conversation()
            await pilot.pause()
            second = app.active_conversation
            second_agent = object()
            second.state.agent = second_agent
            if change == "close":
                app._close_conversation(first)
            elif change == "cd":
                first.state.cwd = tmp_path / "new-cwd"
            finished.set()
            await worker.wait()
            await pilot.pause()
            assert second.state.cwd == tmp_path
            assert second.state.agent is second_agent
            assert app.active_conversation is second
            if change == "switch":
                assert first.state.cwd == root
                assert first.state.agent is None
                await asyncio.to_thread(app.session_saver.flush)
                saved = first.state.session_store.get_session(
                    session_id=first.state.session_id, user_id=first.state.user_id,
                )
                assert saved.metadata["tui"]["cwd"] == str(root)
            else:
                assert first.state.cwd == (tmp_path if change == "close" else tmp_path / "new-cwd")
                assert first.state.agent is original_agent
            assert app._codebase_scan_context is None
            assert app._codebase_scan_progress is None

    asyncio.run(scenario())


def test_superseded_scan_cannot_clear_new_scan_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        finished = asyncio.Event()

        async def scan(self, root):
            await finished.wait()
            return CodebaseScanResult(root, 1, 1, 0, 1, root / "db")

        monkeypatch.setattr(MTPApp, "_run_codebase_scan_worker", scan)
        monkeypatch.setattr(MTPApp, "_request_background_memory_refresh", lambda *args, **kwargs: None)
        app = MTPApp(state=_make_state(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            first = app.active_conversation
            app._start_codebase_scan(tmp_path / "old", app.active_chat_log)
            old_worker = app._codebase_scan_context[0]
            await pilot.pause()
            app.action_new_conversation()
            await pilot.pause()
            second = app.active_conversation
            app._start_codebase_scan(tmp_path / "new", app.active_chat_log)
            new_worker = app._codebase_scan_context[0]
            await pilot.pause()
            assert old_worker.is_cancelled
            assert app._codebase_scan_context[0] is new_worker
            assert app._codebase_scan_progress is not None
            finished.set()
            await new_worker.wait()
            await pilot.pause()
            assert first.state.cwd == tmp_path
            assert second.state.cwd == tmp_path / "new"

    asyncio.run(scenario())
