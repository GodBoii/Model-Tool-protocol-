from __future__ import annotations

import pytest

from mtp.exceptions import RetryAgentRun, StopAgentRun


class TestRetryAgentRun:
    def test_is_runtime_error(self):
        exc = RetryAgentRun("please retry")
        assert isinstance(exc, RuntimeError)
        assert str(exc) == "please retry"

    def test_default_message(self):
        exc = RetryAgentRun()
        assert str(exc) == ""


class TestStopAgentRun:
    def test_is_runtime_error(self):
        exc = StopAgentRun("stop now")
        assert isinstance(exc, RuntimeError)
        assert str(exc) == "stop now"

    def test_default_message(self):
        exc = StopAgentRun()
        assert str(exc) == ""
