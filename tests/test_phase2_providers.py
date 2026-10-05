"""Hosted provider and Responses contracts with no external inference requests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from mtp.agent import Agent, AgentAction
from mtp.cli.main import build_parser
from mtp.cli.providers import get_provider
from mtp.cli.tui_provider_factory import ProviderSelection, build_tui_provider
from mtp.protocol import ToolSpec
from mtp.providers import (
    DashScope,
    DeepInfra,
    HuggingFace,
    OpenAICompatible,
    OpenAIResponses,
)
from mtp.runtime import ToolRegistry
from mtp.session_store import JsonSessionStore

HOSTED = [HuggingFace, DeepInfra, DashScope]
SPEC = ToolSpec(
    "math.echo",
    "Return a value",
    {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    },
)


def chat_response(name, *, dependent=False):
    calls = [
        {
            "id": "a",
            "type": "function",
            "function": {"name": name, "arguments": '{"value":7}'},
        }
    ]
    calls.append(
        {
            "id": "b",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps({"value": {"$ref": 0} if dependent else 9}),
            },
        }
    )
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": calls,
                    "reasoning_content": "opaque",
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    }


class Stream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.chunks)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("cls", HOSTED)
@pytest.mark.parametrize("dependent", [True, False])
def test_hosted_batch_execution_and_native_replay(cls, dependent):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return chat_response(
            kwargs["tools"][0]["function"]["name"], dependent=dependent
        )

    provider = cls(client=NS(chat=NS(completions=NS(create=create))))
    action = provider.next_action([{"role": "user", "content": "echo"}], [SPEC])
    registry = ToolRegistry()
    registry.register_tool(SPEC, lambda value: value)
    results = asyncio.run(registry.execute_plan(action.plan))
    assert [r.output for r in results] == ([7, 7] if dependent else [7, 9])
    assert action.metadata["provider"] in {"huggingface", "deepinfra", "dashscope"}
    replay = provider._to_openai_messages([action.metadata["assistant_tool_message"]])
    assert replay[0]["reasoning_content"] == "opaque"
    assert (
        replay[0]["tool_calls"][0]["function"]["name"]
        == requests[0]["tools"][0]["function"]["name"]
    )
    assert "temperature" not in requests[0]


@pytest.mark.parametrize("cls", HOSTED)
def test_interleaved_stream_fragments_and_usage(cls):
    stream = Stream(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 1,
                                    "id": "b",
                                    "function": {
                                        "name": "math_",
                                        "arguments": '{"value":',
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "a",
                                    "function": {
                                        "name": "math_echo",
                                        "arguments": '{"value":7}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 1,
                                    "function": {"name": "echo", "arguments": "9}"},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 4}},
        ]
    )
    provider = cls(client=NS(chat=NS(completions=NS(create=lambda **kwargs: stream))))
    items = list(provider.stream_next_action([], [ToolSpec("math_echo", "echo")]))
    calls = items[-1].plan.batches[0].calls
    assert [c.id for c in calls] == ["a", "b"]
    assert [c.arguments for c in calls] == [{"value": 7}, {"value": 9}]
    assert items[-1].metadata["usage"]["output_tokens"] == 4
    assert stream.closed


@pytest.mark.parametrize(
    "chunks",
    [
        [{"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]}],
        [{"choices": [{"delta": {}, "finish_reason": "length"}]}],
        [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "a",
                                    "function": {"name": "echo", "arguments": "broken"},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ],
    ],
)
def test_broken_stream_closes_without_returning_executable_plan(chunks):
    stream = Stream(chunks)
    provider = HuggingFace(
        client=NS(chat=NS(completions=NS(create=lambda **kwargs: stream)))
    )
    items = []
    with pytest.raises(ValueError):
        for item in provider.stream_next_action([], [SPEC]):
            items.append(item)  # noqa: PERF402 - retain items yielded before a failing stream
    assert not any(isinstance(x, AgentAction) and x.plan for x in items)
    assert stream.closed


@pytest.mark.parametrize(
    "url",
    [
        "https://user:key@example.com/v1",
        "http://remote.example/v1",
        "https://example.com/v1?key=secret",
        "ftp://example.com",
        "https://example.com/v1#fragment",
    ],
)
def test_custom_endpoints_reject_credentials_and_insecure_remote_urls(url):
    with pytest.raises(ValueError):
        OpenAICompatible(model="example", base_url=url, client=NS())


@pytest.mark.parametrize(
    "extra",
    [{"tools": []}, {"model": "other"}, {"api_key": "secret"}, {"stream": False}],
)
def test_extra_body_cannot_override_protocol_fields(extra):
    with pytest.raises(ValueError):
        HuggingFace(client=NS(), extra_body=extra)


def test_dashscope_regions_and_stream_only_thinking():
    provider = DashScope(
        client=NS(), workspace_id="ws-123", region="beijing", enable_thinking=True
    )
    assert (
        provider.base_url
        == "https://ws-123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    with pytest.raises(ValueError):
        provider._request([], [])
    request = provider._request([], [SPEC], stream=True)
    assert request["extra_body"]["enable_thinking"] is True
    assert request["parallel_tool_calls"] is True
    with pytest.raises(ValueError):
        DashScope(client=NS(), workspace_id="ws/secret")


def responses_body(wire="echo", *, call=True, status="completed"):
    items = [
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [],
            "encrypted_content": "opaque-encrypted",
        }
    ]
    if call:
        items += [
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call1",
                "name": wire,
                "arguments": '{"value":7}',
                "status": "completed",
            }
        ]
    else:
        items += [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "7", "annotations": []}],
            }
        ]
    return {
        "status": status,
        "output": items,
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    }


def test_responses_persists_all_native_items_and_correlates_call_ids(tmp_path):
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return (
            responses_body(kwargs["tools"][0]["name"])
            if len(captured) == 1
            else responses_body(call=False)
        )

    provider = OpenAIResponses(
        client=NS(responses=NS(create=create)), reasoning_effort="low"
    )
    registry = ToolRegistry()
    registry.register_tool(SPEC, lambda value: value)
    store = JsonSessionStore(db_path=tmp_path)
    output = Agent(provider=provider, tools=registry, session_store=store).run_output(
        "echo", max_rounds=2, session_id="s"
    )
    assert output.final_text == "7" and output.tool_results[0].output == 7
    assert captured[0]["store"] is False and captured[0]["include"] == [
        "reasoning.encrypted_content"
    ]
    assert captured[0]["tools"][0]["strict"] is False
    assert "temperature" not in captured[0]
    replay = [
        i
        for i in captured[1]["input"]
        if i.get("type") in {"reasoning", "function_call", "function_call_output"}
    ]
    assert replay[0]["encrypted_content"] == "opaque-encrypted"
    assert replay[1]["call_id"] == replay[2]["call_id"] == "call1"
    restored = OpenAIResponses(client=NS())
    serialized = restored._input(store.get_session("s").messages)
    assert any(i.get("encrypted_content") == "opaque-encrypted" for i in serialized)
    assert captured[1]["reasoning"] == {"effort": "low"}


def test_responses_stream_waits_for_completed_items_then_executes():
    stream = Stream(
        [
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "arguments": ""},
            },
            {"type": "response.function_call_arguments.delta", "delta": "{"},
            {"type": "response.completed", "response": responses_body()},
        ]
    )
    provider = OpenAIResponses(client=NS(responses=NS(create=lambda **kwargs: stream)))
    items = list(provider.stream_next_action([], [ToolSpec("echo", "echo")]))
    assert len(items) == 1 and items[0].plan.batches[0].calls[0].id == "call1"
    assert stream.closed


@pytest.mark.parametrize("kind", ["response.failed", "response.incomplete", "error"])
def test_responses_failure_is_not_reported_as_success(kind):
    stream = Stream([{"type": kind}])
    provider = OpenAIResponses(client=NS(responses=NS(create=lambda **kwargs: stream)))
    with pytest.raises(ValueError):
        list(provider.stream_next_action([], []))
    assert stream.closed


def test_responses_missing_terminal_event_fails():
    provider = OpenAIResponses(
        client=NS(responses=NS(create=lambda **kwargs: iter([])))
    )
    with pytest.raises(ValueError):
        list(provider.stream_next_action([], []))


def test_duplicate_ids_are_rejected_before_calls_can_be_dropped():
    body = chat_response("echo")
    body["choices"][0]["message"]["tool_calls"][1]["id"] = "a"
    provider = HuggingFace(
        client=NS(chat=NS(completions=NS(create=lambda **kwargs: body)))
    )
    with pytest.raises(ValueError, match="duplicate"):
        provider.next_action([], [ToolSpec("echo", "echo")])


def test_identical_reads_do_not_skip_a_second_calls_dependency_or_approval():
    from mtp.policy import PolicyDecision, RiskPolicy
    from mtp.protocol import (
        ExecutionPlan,
        ToolBatch,
        ToolCall,
        ToolResult,
        ToolRiskLevel,
    )

    prior = {
        "failed": ToolResult("failed", "fail", None, success=False, error="failed")
    }
    registry = ToolRegistry(
        policy=RiskPolicy(by_risk={ToolRiskLevel.READ_ONLY: PolicyDecision.ASK}),
        approval_handler=lambda spec, call, args: call.id == "approved",
    )
    registry.register_tool(SPEC, lambda value: value)
    plan = ExecutionPlan(
        [
            ToolBatch(
                "parallel",
                [
                    ToolCall("approved", SPEC.name, {"value": 7}),
                    ToolCall("denied", SPEC.name, {"value": 7}),
                    ToolCall(
                        "dependent", SPEC.name, {"value": 7}, depends_on=["failed"]
                    ),
                ],
            )
        ]
    )
    results = asyncio.run(registry.execute_plan(plan, prior_results=prior))
    assert [r.success for r in results] == [True, False, False]
    assert results[1].skipped and results[2].skipped


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress"])
def test_responses_unsuccessful_status_is_rejected(status):
    provider = OpenAIResponses(
        client=NS(responses=NS(create=lambda **kwargs: responses_body(status=status)))
    )
    with pytest.raises(ValueError):
        provider.next_action([], [SPEC])


@pytest.mark.parametrize(
    "field", ["store", "include", "previous_response_id", "reasoning"]
)
def test_responses_extra_body_cannot_change_stateless_replay(field):
    with pytest.raises(ValueError):
        OpenAIResponses(client=NS(), extra_body={field: "override"})


def test_catalog_respects_configured_regional_endpoint(monkeypatch):
    from mtp.cli import tui_model_catalog as catalog

    captured = []

    def request(url, headers, timeout):
        captured.append((url, headers))
        return {"data": [{"id": "regional-model"}]}

    monkeypatch.setattr(catalog, "_request_json", request)
    settings = {
        "providers": {
            "dashscope": {
                "api_key": "synthetic-key",
                "base_url": "https://ws-1.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            }
        }
    }
    result = catalog.discover_provider_models("dashscope", settings)
    assert result.models == ("regional-model",)
    assert captured[0][0] == settings["providers"]["dashscope"]["base_url"] + "/models"
    assert "synthetic-key" not in captured[0][0]


def test_hf_catalog_preserves_explicit_routing_choices():
    from mtp.cli.tui_model_catalog import _model_ids

    rows = {
        "data": [
            {
                "id": "org/model",
                "providers": [
                    {"provider": "cerebras", "status": "live", "supports_tools": True},
                    {
                        "provider": "retired",
                        "status": "staging",
                        "supports_tools": True,
                    },
                    {"provider": "no-tools", "status": "live", "supports_tools": False},
                ],
            }
        ]
    }
    assert _model_ids("huggingface", rows) == ["org/model", "org/model:cerebras"]


@pytest.mark.parametrize(
    "provider", ["huggingface", "deepinfra", "dashscope", "openai_responses"]
)
def test_tui_endpoint_budget_setup_keeps_models_and_credentials_local(
    tmp_path, provider, monkeypatch
):
    from test_tui_app_lifecycle import _make_state
    from textual.widgets import Input

    from mtp.cli.tui_app import MTPApp
    from mtp.cli.tui_settings import (
        PROVIDER_KEY_ENV,
        load_provider_settings,
        provider_settings_path,
    )
    from mtp.cli.tui_widgets.provider_setup import ProviderSetup

    monkeypatch.delenv(PROVIDER_KEY_ENV[provider], raising=False)

    async def scenario():
        state = _make_state(tmp_path)
        state.backend = provider
        app = MTPApp(state=state)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, ProviderSetup)
            dialog.query_one("#setup-key", Input).value = "synthetic-key"
            dialog.query_one(
                "#setup-endpoint", Input
            ).value = "https://regional.example/v1"
            dialog.query_one("#setup-budget", Input).value = "256"
            dialog.query_one("#setup-model", Input).value = "regional-model"
            key_input = dialog.query_one("#setup-key", Input)
            dialog._save()
            await pilot.pause()
            saved = load_provider_settings(
                provider_settings_path(state.session_store.file_path)
            )["providers"][provider]
            assert saved["base_url"] == "https://regional.example/v1"
            assert (
                saved[
                    "max_output_tokens"
                    if provider == "openai_responses"
                    else "max_tokens"
                ]
                == 256
            )
            assert saved["model"] == "regional-model"
            assert key_input.value == ""
            assert not app._input_history and not state.transcript

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "provider,alias,env",
    [
        ("huggingface", "HuggingFace", "HF_TOKEN"),
        ("deepinfra", "DeepInfra", "DEEPINFRA_API_KEY"),
        ("dashscope", "DashScope", "DASHSCOPE_API_KEY"),
        ("openai_responses", "OpenAIResponses", "OPENAI_API_KEY"),
    ],
)
def test_cli_registration_and_tui_constructor(provider, alias, env):
    info = get_provider(provider)
    assert info.alias == alias and info.env_var == env
    assert build_parser().parse_args(["tui", "--backend", provider]).backend == provider
    instance = build_tui_provider(
        ProviderSelection(provider, "fixture-model", "synthetic-key", None)
    )
    assert instance.model == "fixture-model" and instance.provider_name == provider
    instance._client.close()
