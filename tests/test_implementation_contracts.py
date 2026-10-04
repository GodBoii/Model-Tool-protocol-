"""Behavior checks beyond the original audit repros."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from mtp.cli.tui_settings import (
    DEFAULT_PROVIDER_MODELS,
    get_provider_models,
    preferred_model_for_provider,
)
from mtp.protocol import ToolSpec
from mtp.providers.defaults import DEFAULT_PROVIDER_MODELS as SDK_DEFAULTS
from mtp.providers.groq_provider import GroqToolCallingProvider
from mtp.providers.tool_names import ToolNameMap


def test_openai_dotted_tool_names_execute_and_replay_with_wire_identifiers():
    from mtp.providers.openai_provider import OpenAIToolCallingProvider
    from mtp.runtime import ToolRegistry

    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        wire = kwargs["tools"][0]["function"]["name"]
        return NS(
            choices=[
                NS(
                    message=NS(
                        content="",
                        tool_calls=[
                            NS(
                                id="wire1",
                                function=NS(name=wire, arguments='{"a":1,"b":2}'),
                            )
                        ],
                    )
                )
            ]
        )

    provider = OpenAIToolCallingProvider(
        client=NS(chat=NS(completions=NS(create=create))),
        tool_choice={"type": "function", "function": {"name": "calculator.add"}},
    )
    spec = ToolSpec("calculator.add", "add")
    action = provider.next_action([], [spec])
    assert (
        captured[0]["tool_choice"]["function"]["name"]
        == captured[0]["tools"][0]["function"]["name"]
    )
    registry = ToolRegistry()
    registry.register_tool(spec, lambda a, b: a + b)
    assert asyncio.run(registry.execute_plan(action.plan))[0].output == 3
    replay = provider._to_openai_messages([action.metadata["assistant_tool_message"]])
    assert (
        replay[0]["tool_calls"][0]["function"]["name"]
        == captured[0]["tools"][0]["function"]["name"]
    )


def test_tool_name_mapping_is_stable_collision_safe_and_reversible():
    names = [
        "calculator.add",
        "calculator__add",
        "mtp_calculator_add",
        "x" * 200,
        "工具.echo",
    ]
    first = ToolNameMap()
    forward = {name: first.wire_name(name) for name in names}
    second = ToolNameMap()
    assert forward == {name: second.wire_name(name) for name in reversed(names)}
    assert len(set(forward.values())) == len(names)
    assert all(
        first.original_name(wire) == original for original, wire in forward.items()
    )


@pytest.mark.parametrize("provider", DEFAULT_PROVIDER_MODELS)
def test_sdk_tui_defaults_match_without_duplicate_suggestions(provider):
    assert (
        DEFAULT_PROVIDER_MODELS[provider]
        == SDK_DEFAULTS["anthropic" if provider == "claude" else provider]
    )
    payload = {"providers": {provider: {"model": "user-custom"}}}
    choices = get_provider_models(payload, provider)
    assert len(choices) == len(set(choices))
    assert preferred_model_for_provider(payload, provider) == "user-custom"


def test_groq_budget_reaches_planning_finalization_and_streaming():
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        response = NS(choices=[NS(message=NS(content="done", tool_calls=None))])
        return iter([]) if kwargs.get("stream") else response

    provider = GroqToolCallingProvider(
        client=NS(chat=NS(completions=NS(create=create))), max_completion_tokens=128
    )
    provider.next_action([], [])
    provider.finalize([], [])
    list(provider.finalize_stream([], []))
    assert [r["max_completion_tokens"] for r in captured] == [128, 128, 128]
    assert (
        not GroqToolCallingProvider(model="openai/gpt-oss-120b", client=NS())
        .capabilities()
        .supports_parallel_tool_calls
    )


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_groq_rejects_invalid_output_budgets(budget):
    with pytest.raises(ValueError):
        GroqToolCallingProvider(client=NS(), max_completion_tokens=budget)


def test_deepseek_preserves_reasoning_even_when_display_capture_is_off():
    from mtp.providers.deepseek_provider import DeepSeekToolCallingProvider

    response = NS(
        choices=[
            NS(
                message=NS(
                    content="",
                    reasoning_content="opaque-state",
                    tool_calls=[
                        NS(id="wire1", function=NS(name="echo", arguments="{}")),
                    ],
                )
            )
        ]
    )
    provider = DeepSeekToolCallingProvider(
        client=NS(chat=NS(completions=NS(create=lambda **kwargs: response))),
        capture_reasoning=False,
    )
    action = provider.next_action([], [ToolSpec("echo", "echo")])
    replay = provider._to_deepseek_messages([action.metadata["assistant_tool_message"]])
    assert replay[0]["reasoning_content"] == "opaque-state"
    assert "reasoning" not in action.metadata


def test_gemini_persisted_signature_and_function_id_round_trip(tmp_path):
    types = pytest.importorskip("google.genai.types")
    from mtp.providers.gemini_provider import GeminiToolCallingProvider
    from mtp.session_store import JsonSessionStore, SessionRecord

    part = types.Part.from_function_call(name="echo", args={"value": 7})
    part.function_call.id = "wire1"
    part.thought_signature = b"\xff\x01opaque"
    provider = GeminiToolCallingProvider(
        client=NS(
            models=NS(
                generate_content=lambda **kwargs: NS(
                    text="",
                    candidates=[NS(content=types.Content(role="model", parts=[part]))],
                )
            )
        )
    )
    action = provider.next_action([], [ToolSpec("echo", "echo")])
    messages = [
        action.metadata["assistant_tool_message"],
        {
            "role": "tool",
            "tool_name": "echo",
            "tool_call_id": "wire1",
            "content": "7",
        },
    ]
    store = JsonSessionStore(db_path=tmp_path)
    store.upsert_session(SessionRecord(session_id="signed", messages=messages))
    restored = GeminiToolCallingProvider(client=NS())
    contents, _ = restored._to_gemini_payload(store.get_session("signed").messages)
    assert contents[0].parts[0].thought_signature == part.thought_signature
    assert contents[1].parts[0].function_response.id == "wire1"


@pytest.mark.parametrize("provider", ["ollama", "lmstudio"])
def test_async_sdk_stream_finishes_without_stop_iteration_in_future(provider):
    from mtp.providers.lmstudio_provider import LMStudioToolCallingProvider
    from mtp.providers.ollama_provider import OllamaToolCallingProvider

    if provider == "ollama":
        adapter = OllamaToolCallingProvider(
            client=NS(chat=lambda **kwargs: iter([{"message": {"content": "ok"}}]))
        )
    else:
        chunks = [NS(choices=[NS(delta=NS(content="ok", tool_calls=None))])]
        adapter = LMStudioToolCallingProvider(
            client=NS(chat=NS(completions=NS(create=lambda **kwargs: iter(chunks))))
        )

    async def collect():
        return [item async for item in adapter.astream_next_action([], [])]

    items = asyncio.run(asyncio.wait_for(collect(), 2))
    assert items[-1].response_text == "ok"


def test_python_toolkit_restricts_execution_unless_opted_in(tmp_path):
    from mtp.toolkits.python_toolkit import PythonToolkit

    toolkit = PythonToolkit(base_dir=tmp_path)
    tools = {tool.spec.name: tool.handler for tool in toolkit.load_tools()}
    assert tools["python.run_code"](code="result = sum(range(5))") == 10
    with pytest.raises(ValueError):
        tools["python.run_code"](code="import os")
    with pytest.raises(ValueError):
        tools["python.run_code"](code="result = (x for x in [1]).gi_frame.f_back")
    with pytest.raises(TypeError):
        PythonToolkit(base_dir=tmp_path, allow_unsafe_exec="false")
    with pytest.raises(RuntimeError):
        tools["python.run_code"](code="result = open('private.txt')")
    unsafe = PythonToolkit(base_dir=tmp_path, allow_unsafe_exec=True)
    assert (
        unsafe._run_in_subprocess("import math; result = math.sqrt(9)", "result") == 3
    )


def test_workspace_permission_does_not_autoapprove_arbitrary_shell(tmp_path):
    from test_audit_regressions import ScriptedProvider, native_action

    from mtp.cli.tui_harness_agent import build_harness_agent

    agent = build_harness_agent(
        provider=ScriptedProvider(
            [
                native_action(
                    [
                        {
                            "id": "shell1",
                            "function": {
                                "name": "shell.run",
                                "arguments": '{"command":"python unknown.py"}',
                            },
                        }
                    ]
                )
            ]
        ),
        cwd=tmp_path,
        mode="code",
        autoresearch=False,
        research_instructions=None,
        sandbox_mode="workspace-write",
    )
    output = agent.run_output("synthetic", max_rounds=1)
    assert output.tool_results[0].skipped is True


def test_workspace_read_only_syntax_check_loads_its_actual_owner(tmp_path):
    from test_audit_regressions import ScriptedProvider, native_action

    from mtp.cli.tui_harness_agent import build_harness_agent

    (tmp_path / "example.py").write_text("value = 3\n", encoding="utf-8")
    provider = ScriptedProvider(
        [
            native_action(
                [
                    {
                        "id": "syntax1",
                        "function": {
                            "name": "agent.syntax_check",
                            "arguments": '{"path":"example.py"}',
                        },
                    }
                ]
            )
        ]
    )
    agent = build_harness_agent(
        provider=provider,
        cwd=tmp_path,
        mode="code",
        autoresearch=False,
        research_instructions=None,
        sandbox_mode="workspace-write",
    )
    result = agent.run_output("check", max_rounds=1).tool_results[0]
    assert (
        result.success and result.output["checked"] == 1 and not result.output["errors"]
    )


def test_together_does_not_retry_authentication_failure():
    from mtp.providers.together_provider import TogetherAIToolCallingProvider

    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        raise PermissionError("synthetic unauthorized")

    provider = TogetherAIToolCallingProvider(
        client=NS(chat=NS(completions=NS(create=create)))
    )
    with pytest.raises(PermissionError):
        provider.next_action([], [ToolSpec("echo", "echo")])
    assert len(calls) == 1
