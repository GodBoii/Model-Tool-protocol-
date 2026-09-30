"""A rebuilt agent continues the transcript without replaying provider tools."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from mtp.agent import AgentAction
from mtp.simple_agent import MTPAgent
from mtp.runtime import ToolRegistry
from mtp.cli import tui_harness_agent, tui_provider_factory
from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_settings import provider_settings_path, save_provider_settings
from mtp.cli.tui_state import TranscriptTurn
from mtp.cli.tui_workers import (
    apply_backend_switch, prepare_backend_switch, record_turn, run_prompt_blocking, seed_history,
)
from test_tui_app_lifecycle import _make_state


class RecordingProvider:
    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    def next_action(self, messages, tools):
        self.requests.append([dict(message) for message in messages])
        return AgentAction(response_text="remembered reply")

    def finalize(self, messages, tool_results):
        return "remembered reply"


def _agent(provider=None, **kwargs) -> MTPAgent:
    return MTPAgent(provider=provider or RecordingProvider(), tools=ToolRegistry(), instructions="system", **kwargs)


def _turn(prompt="first prompt", response="first reply", **kwargs) -> TranscriptTurn:
    return TranscriptTurn(
        prompt=prompt, response=response, backend="groq", model="test",
        attachments=[], warnings=[], usage_lines=[], created_at="now", **kwargs,
    )


@pytest.fixture
def configured_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = _make_state(tmp_path)
    state.backend = "groq"
    save_provider_settings(provider_settings_path(state.session_store.file_path), {
        "providers": {
            name: {"api_key": "test-key", "model": "test-model"}
            for name in ("groq", "openai")
        },
    })
    providers: list[RecordingProvider] = []

    def build_provider(selection):
        provider = RecordingProvider()
        providers.append(provider)
        return provider

    monkeypatch.setattr(tui_provider_factory, "build_tui_provider", build_provider)
    monkeypatch.setattr(tui_harness_agent, "build_harness_agent", lambda **kwargs: _agent(kwargs["provider"]))
    return state, providers


def test_model_command_preserves_history(configured_state) -> None:
    state, providers = configured_state
    result = run_prompt_blocking(state, "first prompt")
    assert result.status == "completed", result.error
    record_turn(state, "first prompt", result, persist=False)

    async def scenario() -> None:
        app = MTPApp(state=state)
        async with app.run_test(size=(120, 40)):
            app._dispatch_command("model", "replacement-model")
            assert state.agent is None
            second = await asyncio.to_thread(run_prompt_blocking, state, "second prompt")
            assert second.status == "completed"

    asyncio.run(scenario())
    request = providers[-1].requests[0]
    assert [message["role"] for message in request[:2]] == ["system", "system"]
    assert [message["content"] for message in request if message["role"] != "system"] == [
        "first prompt", "remembered reply", "second prompt",
    ]


def test_backend_switch_seeds_latest_transcript_on_first_run(configured_state) -> None:
    state, providers = configured_state
    switch = prepare_backend_switch(state, "openai")
    # A run can finish while the backend is being prepared.
    state.transcript.append(_turn())
    assert apply_backend_switch(state, switch)
    result = run_prompt_blocking(state, "next prompt")
    assert result.status == "completed", result.error
    request = providers[-1].requests[0]
    assert [message["content"] for message in request if message["role"] != "system"] == [
        "first prompt", "first reply", "next prompt",
    ]
    record_turn(state, "next prompt", result, persist=False)
    assert run_prompt_blocking(state, "third prompt").status == "completed"
    assert sum(message["content"] == "first prompt" for message in providers[-1].requests[-1]) == 1


def test_loaded_transcript_is_seeded(configured_state) -> None:
    state, providers = configured_state
    state.transcript = [_turn(status="cancelled", response="partial", tool_details=[{"tool_name": "old.tool"}])]
    result = run_prompt_blocking(state, "continue")
    assert result.status == "completed", result.error
    request = providers[-1].requests[0]
    assert any(message["content"] == state.transcript[0].history_reply() for message in request)
    assert all(message["role"] in {"system", "user", "assistant"} for message in request)
    assert all("tool_calls" not in message for message in request)


def test_seed_keeps_recent_whole_pairs_with_message_limit() -> None:
    agent = _agent(max_history_messages=8)
    turns = [_turn(prompt=str(index)) for index in range(10)]
    seed_history(agent, turns, provider_name="groq", model_name="test")
    assert [message["content"] for message in agent._agent.messages if message["role"] == "user"] == ["8", "9"]
    before = list(agent._agent.messages)
    seed_history(agent, turns, provider_name="groq", model_name="test")
    assert agent._agent.messages == before


def test_seed_respects_small_context_and_current_prompt() -> None:
    agent = _agent()
    turns = [_turn(prompt="x" * 20000), _turn(prompt="recent")]
    seed_history(agent, turns, provider_name="ollama", model_name="gemma2:2b", prompt="x" * 10000)
    assert [message["content"] for message in agent._agent.messages if message["role"] == "user"] == ["recent"]
    empty = _agent()
    seed_history(empty, turns, provider_name="ollama", model_name="gemma2:2b", prompt="x" * 20000)
    assert all(message["role"] == "system" for message in empty._agent.messages)


def test_seed_does_not_change_existing_agent_history() -> None:
    agent = _agent()
    agent._agent.messages = [{"role": "user", "content": "already present"}]
    seed_history(agent, [_turn()], provider_name="groq", model_name="test")
    assert agent._agent.messages == [{"role": "user", "content": "already present"}]
