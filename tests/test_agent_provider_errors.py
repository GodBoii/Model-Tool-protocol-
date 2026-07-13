from __future__ import annotations

import pytest

from mtp import Agent, ProviderError, ProviderErrorCategory, ToolRegistry


class _LeakyProvider:
    def next_action(self, messages, tools):
        error = RuntimeError("request failed with secret-key-123 and private prompt")
        error.status_code = 429
        raise error

    def finalize(self, messages, tool_results):
        raise AssertionError("not reached")


def test_agent_normalizes_unintegrated_provider_errors_without_secret_leak() -> None:
    agent = Agent(provider=_LeakyProvider(), registry=ToolRegistry())

    with pytest.raises(ProviderError) as captured:
        agent.run_output("private prompt")

    error = captured.value
    assert error.category is ProviderErrorCategory.RATE_LIMIT
    assert error.retryable is True
    assert error.status_code == 429
    assert "secret-key-123" not in str(error)
    assert "private prompt" not in str(error)
