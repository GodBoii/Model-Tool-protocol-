from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp.providers.common import ToolArgumentsParseError, openai_like_tool_call_plan_payload, safe_load_arguments


@pytest.mark.parametrize("value", ["{bad secret-value", "[]", "42", '"text"'])
def test_malformed_or_nonobject_tool_arguments_are_never_executable(value: str) -> None:
    with pytest.raises(ToolArgumentsParseError) as captured:
        safe_load_arguments(value)
    assert "secret-value" not in str(captured.value)


def test_openai_like_plan_rejects_malformed_tool_arguments() -> None:
    call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="dangerous.write", arguments="{not-json"),
    )
    with pytest.raises(ToolArgumentsParseError):
        openai_like_tool_call_plan_payload(
            provider="test", model="test", tool_calls=[call]
        )
