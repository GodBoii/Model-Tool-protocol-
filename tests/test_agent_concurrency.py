from __future__ import annotations

import threading

import pytest

from mtp import Agent, ConcurrentRunError, ToolRegistry
from mtp.agent import AgentAction


class _BlockingProvider:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def next_action(self, messages, tools):
        self.entered.set()
        assert self.release.wait(2)
        return AgentAction(response_text="done")

    def finalize(self, messages, tool_results):
        return "done"


def test_agent_rejects_overlapping_runs_instead_of_corrupting_history() -> None:
    provider = _BlockingProvider()
    agent = Agent(provider=provider, registry=ToolRegistry())
    outcome: list[object] = []

    thread = threading.Thread(target=lambda: outcome.append(agent.run_output("first")))
    thread.start()
    assert provider.entered.wait(1)

    with pytest.raises(ConcurrentRunError, match="already has an active run"):
        agent.run_output("second")

    provider.release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert len(outcome) == 1


def test_agent_allows_a_new_run_after_previous_run_finishes() -> None:
    provider = _BlockingProvider()
    provider.release.set()
    agent = Agent(provider=provider, registry=ToolRegistry())

    assert agent.run_output("first").final_text == "done"
    assert agent.run_output("second").final_text == "done"
