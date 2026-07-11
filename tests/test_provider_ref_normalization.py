from __future__ import annotations

from types import SimpleNamespace

from mtp.providers.common import openai_like_tool_call_plan_payload


def _tool_call(call_id: str, name: str, arguments: str):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_one_based_call_placeholder_targets_prior_call() -> None:
    payload = openai_like_tool_call_plan_payload(
        provider="groq",
        model="test",
        tool_calls=[
            _tool_call("generated-first", "calculator.multiply", '{"a": 2, "b": 3}'),
            _tool_call("generated-second", "calculator.add", '{"a": {"$ref": "call_1"}, "b": 4}'),
        ],
    )

    calls = [call for batch in payload["plan"].batches for call in batch.calls]
    assert calls[1].arguments["a"] == {"$ref": "generated-first"}
    assert calls[1].depends_on == ["generated-first"]


def test_exact_provider_call_id_is_not_reinterpreted() -> None:
    payload = openai_like_tool_call_plan_payload(
        provider="test",
        model="test",
        tool_calls=[
            _tool_call("call_1", "first", "{}"),
            _tool_call("call_2", "second", '{"value": {"$ref": "call_1"}}'),
        ],
    )

    calls = [call for batch in payload["plan"].batches for call in batch.calls]
    assert calls[1].depends_on == ["call_1"]
