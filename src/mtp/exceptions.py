from __future__ import annotations


class RetryAgentRun(RuntimeError):
    """Ask the agent loop to retry after giving model feedback."""


class StopAgentRun(RuntimeError):
    """Ask the agent loop to stop tool execution and pause/return."""

