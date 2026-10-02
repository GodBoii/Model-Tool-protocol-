"""Audit repros. Strict xfails document unresolved defects, not passing behavior."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
from types import SimpleNamespace as NS

import pytest

from mtp.agent import Agent, AgentAction
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolRiskLevel, ToolSpec
from mtp.providers.common import openai_like_tool_call_plan_payload
from mtp.runtime import ToolRegistry
from mtp.schema import PlanValidationError

ADAPTERS = [
    ("openai", "OpenAIToolCallingProvider"),
    ("groq", "GroqToolCallingProvider"),
    ("openrouter", "OpenRouterToolCallingProvider"),
    ("anthropic", "AnthropicToolCallingProvider"),
    ("gemini", "GeminiToolCallingProvider"),
    ("cohere", "CohereToolCallingProvider"),
    ("mistral", "MistralToolCallingProvider"),
    ("cerebras", "CerebrasToolCallingProvider"),
    ("deepseek", "DeepSeekToolCallingProvider"),
    ("sambanova", "SambaNovaToolCallingProvider"),
    ("together", "TogetherAIToolCallingProvider"),
    ("fireworks", "FireworksAIToolCallingProvider"),
    ("xiaomi", "XiaomiToolCallingProvider"),
    ("ollama", "OllamaToolCallingProvider"),
    ("lmstudio", "LMStudioToolCallingProvider"),
]


def provider_response(provider: str, dependent: bool):
    second = {"$ref": 0} if dependent else 9
    calls = [
        NS(
            id="wire1",
            function=NS(name="audit_echo", arguments=json.dumps({"value": 7})),
        ),
        NS(
            id="wire2",
            function=NS(name="audit_echo", arguments=json.dumps({"value": second})),
        ),
    ]
    if provider == "anthropic":
        return NS(
            content=[
                NS(
                    type="tool_use",
                    id=c.id,
                    name=c.function.name,
                    input=json.loads(c.function.arguments),
                )
                for c in calls
            ]
        )
    if provider == "gemini":
        return NS(
            text="",
            candidates=[
                NS(
                    content=NS(
                        parts=[
                            NS(
                                function_call=NS(
                                    name=c.function.name,
                                    args=json.loads(c.function.arguments),
                                ),
                                text=None,
                            )
                            for c in calls
                        ]
                    )
                )
            ],
        )
    if provider == "cohere":
        return NS(message=NS(tool_calls=calls, content=[]), usage=None)
    if provider == "ollama":
        return {
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "function": {
                            "name": c.function.name,
                            "arguments": json.loads(c.function.arguments),
                        },
                    }
                    for c in calls
                ],
            }
        }
    return NS(choices=[NS(message=NS(tool_calls=calls, content=""))])


@pytest.mark.parametrize("provider,class_name", ADAPTERS)
@pytest.mark.parametrize("dependent", [False, True], ids=["parallel", "sequential"])
def test_all_adapters_execute_native_batches(provider, class_name, dependent):
    """Exercise adapter parsing and the real runtime, without provider network calls."""
    response = provider_response(provider, dependent)
    recorded = []

    def create(**kwargs):
        recorded.append(kwargs)
        return response

    client = NS(
        chat=NS(completions=NS(create=create), complete=create),
        messages=NS(create=create),
        models=NS(generate_content=create),
    )
    if provider == "cohere" or provider == "ollama":
        client.chat = create
    cls = getattr(
        importlib.import_module(f"mtp.providers.{provider}_provider"), class_name
    )
    adapter = cls(client=client)
    spec = ToolSpec(
        "audit_echo",
        "Return value",
        {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    )
    action = adapter.next_action([{"role": "user", "content": "Use the tools"}], [spec])
    assert action.plan is not None
    assert [b.mode for b in action.plan.batches] == (
        ["sequential", "sequential"] if dependent else ["parallel"]
    )
    registry = ToolRegistry()
    registry.register_tool(spec, lambda value: value)
    results = asyncio.run(registry.execute_plan(action.plan))
    assert [r.output for r in results] == ([7, 7] if dependent else [7, 9])
    assert all(r.success for r in results)
    assert recorded


def test_parallel_handlers_overlap_without_timing_assumptions():
    async def probe():
        registry = ToolRegistry()
        entered = []
        barrier = asyncio.Event()

        async def handler(value):
            entered.append(value)
            if len(entered) == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), timeout=2)
            return value

        registry.register_tool(ToolSpec("echo", "echo"), handler)
        plan = ExecutionPlan(
            [
                ToolBatch(
                    "parallel",
                    [
                        ToolCall("a", "echo", {"value": 1}),
                        ToolCall("b", "echo", {"value": 2}),
                    ],
                )
            ]
        )
        results = await registry.execute_plan(plan)
        assert [r.output for r in results] == [1, 2]
        assert all(r.success for r in results)

    asyncio.run(probe())


@pytest.mark.parametrize(
    "calls",
    [
        [ToolCall("a", "echo"), ToolCall("a", "echo")],
        [ToolCall("a", "echo", depends_on=["absent"])],
        [
            ToolCall("a", "echo", depends_on=["b"]),
            ToolCall("b", "echo", depends_on=["a"]),
        ],
    ],
)
def test_invalid_plan_is_rejected_before_side_effects(calls):
    registry = ToolRegistry()
    invoked = []
    registry.register_tool(ToolSpec("echo", "echo"), lambda: invoked.append(True))
    with pytest.raises(PlanValidationError):
        asyncio.run(
            registry.execute_plan(ExecutionPlan([ToolBatch("sequential", calls)]))
        )
    assert not invoked


def test_identical_write_calls_execute_twice():
    invoked = []
    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec("write", "append", risk_level=ToolRiskLevel.WRITE),
        lambda value: invoked.append(value),
    )
    # Explicitly allow this synthetic in-memory write.
    from mtp.policy import PolicyDecision, RiskPolicy

    registry.policy = RiskPolicy(by_risk={ToolRiskLevel.WRITE: PolicyDecision.ALLOW})
    plan = ExecutionPlan(
        [
            ToolBatch(
                "parallel",
                [
                    ToolCall("a", "write", {"value": 1}),
                    ToolCall("b", "write", {"value": 1}),
                ],
            )
        ]
    )
    asyncio.run(registry.execute_plan(plan))
    assert invoked == [1, 1]


def test_failed_prerequisite_blocks_dependent_handler():
    registry = ToolRegistry()
    invoked = []

    def fail():
        raise ValueError("synthetic failure")

    registry.register_tool(ToolSpec("fail", "fail"), fail)
    registry.register_tool(
        ToolSpec("dependent", "dependent"), lambda: invoked.append(True)
    )
    plan = ExecutionPlan(
        [
            ToolBatch(
                "sequential",
                [ToolCall("a", "fail"), ToolCall("b", "dependent", depends_on=["a"])],
            )
        ]
    )
    asyncio.run(registry.execute_plan(plan))
    assert not invoked


class ScriptedProvider:
    def __init__(self, actions: list[AgentAction]) -> None:
        self.actions = iter(actions)

    def next_action(self, messages, tools):
        return next(self.actions, AgentAction(response_text="done"))

    def finalize(self, messages, tool_results):
        return "done"


def native_action(calls):
    payload = openai_like_tool_call_plan_payload(
        provider="audit", model="audit", tool_calls=calls
    )
    return AgentAction(plan=payload["plan"], metadata=payload["metadata"])


def wire_call(call_id, value):
    return {
        "id": call_id,
        "function": {"name": "echo", "arguments": json.dumps({"value": value})},
    }


def test_reference_can_use_a_previous_round_result():
    registry = ToolRegistry()
    registry.register_tool(ToolSpec("echo", "echo"), lambda value: value)
    provider = ScriptedProvider(
        [
            native_action([wire_call("a", 7)]),
            native_action([wire_call("b", {"$ref": "a"})]),
        ]
    )
    output = Agent(provider=provider, tools=registry).run_output(
        "synthetic", max_rounds=3
    )
    assert output.tool_results[0].output == 7


@pytest.mark.parametrize(
    "api", ["run_output", "arun_output", "run_loop_events", "arun_loop_events"]
)
def test_prior_round_references_across_agent_apis(api):
    registry = ToolRegistry()
    registry.register_tool(ToolSpec("echo", "echo"), lambda value: value)
    agent = Agent(
        provider=ScriptedProvider(
            [
                native_action([wire_call("a", 7)]),
                native_action([wire_call("b", {"$ref": "a"})]),
            ]
        ),
        tools=registry,
    )
    if api == "run_output":
        assert agent.run_output("synthetic", max_rounds=3).tool_results[0].output == 7
    elif api == "arun_output":
        assert (
            asyncio.run(agent.arun_output("synthetic", max_rounds=3))
            .tool_results[0]
            .output
            == 7
        )
    else:
        if api == "run_loop_events":
            events = list(
                agent.run_loop_events(
                    "synthetic", max_rounds=3, stream_tool_results=True
                )
            )
        else:

            async def collect():
                return [
                    e
                    async for e in agent.arun_loop_events(
                        "synthetic", max_rounds=3, stream_tool_results=True
                    )
                ]

            events = asyncio.run(collect())
        assert [e["output"] for e in events if e["type"] == "tool_finished"] == [7, 7]


def test_cache_trimming_keeps_tool_history_complete():
    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec("echo", "echo", cache_ttl_seconds=60), lambda value: value
    )
    provider = ScriptedProvider(
        [
            native_action([wire_call("a", 7)]),
            native_action([wire_call("b", 7), wire_call("c", 9)]),
        ]
    )
    output = Agent(provider=provider, tools=registry).run_output(
        "synthetic", max_rounds=3
    )
    declared = {c["id"] for m in output.messages for c in m.get("tool_calls", [])}
    answered = {m["tool_call_id"] for m in output.messages if m.get("role") == "tool"}
    assert declared <= answered


def test_history_limit_preserves_complete_tool_groups():
    registry = ToolRegistry()
    registry.register_tool(ToolSpec("echo", "echo"), lambda value: value)
    provider = ScriptedProvider([native_action([wire_call("a", 7), wire_call("b", 9)])])
    output = Agent(
        provider=provider, tools=registry, max_history_messages=3
    ).run_output("synthetic", max_rounds=2)
    declared = {c["id"] for m in output.messages for c in m.get("tool_calls", [])}
    answered = {m["tool_call_id"] for m in output.messages if m.get("role") == "tool"}
    assert answered <= declared


def test_anthropic_tool_names_meet_official_contract():
    from mtp.providers.anthropic_provider import AnthropicToolCallingProvider

    provider = AnthropicToolCallingProvider(client=NS())
    tools = provider._to_anthropic_tools([ToolSpec("calculator.add", "add")])
    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", tools[0]["name"])


def test_deepseek_reasoner_receives_tools():
    from mtp.providers.deepseek_provider import DeepSeekToolCallingProvider

    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return NS(choices=[NS(message=NS(content="done", tool_calls=None))])

    provider = DeepSeekToolCallingProvider(
        model="deepseek-reasoner", client=NS(chat=NS(completions=NS(create=create)))
    )
    provider.next_action(
        [{"role": "user", "content": "use echo"}], [ToolSpec("echo", "echo")]
    )
    assert captured[0].get("tools")


def test_gemini_preserves_function_call_thought_signature():
    from mtp.providers.gemini_provider import GeminiToolCallingProvider

    client = NS(
        models=NS(
            generate_content=lambda **kwargs: NS(
                text="",
                candidates=[
                    NS(
                        content=NS(
                            parts=[
                                NS(
                                    function_call=NS(name="echo", args={"value": 7}),
                                    text=None,
                                    thought_signature=b"opaque-synthetic",
                                ),
                            ]
                        )
                    )
                ],
            )
        )
    )
    provider = GeminiToolCallingProvider(client=client)
    action = provider.next_action(
        [{"role": "user", "content": "use echo"}], [ToolSpec("echo", "echo")]
    )
    contents, _ = provider._to_gemini_payload(
        [action.metadata["assistant_tool_message"]]
    )
    assert (
        getattr(contents[0].parts[0], "thought_signature", None) == b"opaque-synthetic"
    )


def test_ollama_stream_keeps_calls_from_separate_chunks():
    from mtp.providers.ollama_provider import OllamaToolCallingProvider

    chunks = [
        {
            "message": {
                "tool_calls": [
                    {"function": {"name": "echo", "arguments": {"value": v}}}
                ]
            }
        }
        for v in [7, 9]
    ]
    provider = OllamaToolCallingProvider(client=NS(chat=lambda **kwargs: iter(chunks)))
    actions = list(
        provider.stream_next_action(
            [{"role": "user", "content": "use echo"}], [ToolSpec("echo", "echo")]
        )
    )
    action = actions[-1]
    assert sum(len(b.calls) for b in action.plan.batches) == 2


def test_website_does_not_accept_a_redirect_to_private_network(monkeypatch):
    """Synthetic HTTP boundary; no external or private-network request is sent."""
    import sys

    from mtp.toolkits.website_toolkit import WebsiteToolkit

    toolkit = WebsiteToolkit()
    validated = []

    def validate(url):
        validated.append(url)
        if "127.0.0.1" in url:
            raise ValueError("private target")
        return url

    toolkit._validate_url = validate
    response = NS(
        text="<title>private</title>synthetic private result",
        url="http://127.0.0.1/private",
        raise_for_status=lambda: None,
    )
    monkeypatch.setitem(
        sys.modules, "requests", NS(get=lambda *args, **kwargs: response)
    )
    monkeypatch.setitem(
        sys.modules,
        "bs4",
        NS(
            BeautifulSoup=lambda *args: NS(
                title=None, stripped_strings=["synthetic private result"]
            )
        ),
    )
    with pytest.raises(ValueError, match="private target"):
        toolkit._read_website("https://public.example/start", 100)
    assert validated == ["https://public.example/start", "http://127.0.0.1/private"]


def test_documented_cli_run_positional_path_is_accepted():
    from mtp.cli.main import build_parser

    assert build_parser().parse_args(["run", "my-agent"]).path == "my-agent"


def test_async_events_leave_event_loop_responsive_during_sync_stream():
    import threading

    async def probe():
        started = asyncio.Event()
        loop = asyncio.get_running_loop()
        heartbeat = threading.Event()
        observed = []

        class Provider(ScriptedProvider):
            def finalize_stream(self, messages, tool_results):
                loop.call_soon_threadsafe(started.set)
                observed.append(heartbeat.wait(timeout=0.2))
                yield "done"

        async def beat():
            await started.wait()
            heartbeat.set()

        registry = ToolRegistry()
        registry.register_tool(ToolSpec("echo", "echo"), lambda value: value)
        provider = Provider([native_action([wire_call("a", 7)])])
        agent = Agent(provider=provider, tools=registry)
        task = asyncio.create_task(beat())
        try:
            async for _event in agent.arun_loop_events("synthetic", max_rounds=1):
                pass
            assert observed == [True]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(probe())


def test_fireworks_native_clients_keep_their_own_keys(monkeypatch):
    import sys
    from types import ModuleType

    from mtp.providers.fireworks_provider import FireworksAIToolCallingProvider

    parent = ModuleType("fireworks")
    parent.__path__ = []
    sdk = ModuleType("fireworks.client")
    sdk.api_key = None
    sdk.ChatCompletion = NS(create=lambda **kwargs: sdk.api_key)
    parent.client = sdk
    parent.Fireworks = lambda api_key: NS(
        chat=NS(completions=NS(create=lambda **kwargs: api_key))
    )
    monkeypatch.setitem(sys.modules, "fireworks", parent)
    monkeypatch.setitem(sys.modules, "fireworks.client", sdk)
    first = FireworksAIToolCallingProvider(api_key="synthetic-first")
    FireworksAIToolCallingProvider(api_key="synthetic-second")
    assert first._client.chat.completions.create() == "synthetic-first"


def test_cohere_replays_the_provider_tool_name():
    from mtp.providers.cohere_provider import CohereToolCallingProvider

    response = NS(
        message=NS(
            content=[],
            tool_calls=[
                NS(
                    id="cohere1",
                    function=NS(name="calculator__add", arguments='{"a":1,"b":2}'),
                )
            ],
        ),
        usage=None,
    )
    provider = CohereToolCallingProvider(client=NS(chat=lambda **kwargs: response))
    spec = ToolSpec("calculator.add", "add")
    response.message.tool_calls[0].function.name = provider._to_cohere_tools([spec])[0][
        "function"
    ]["name"]
    action = provider.next_action([{"role": "user", "content": "add"}], [spec])
    replay = provider._to_cohere_messages([action.metadata["assistant_tool_message"]])
    assert (
        replay[0]["tool_calls"][0]["function"]["name"]
        == provider._to_cohere_tools([spec])[0]["function"]["name"]
    )
