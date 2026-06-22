from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any
from uuid import uuid4

import streamlit as st

from mtp import Agent
from mtp.cli.providers import get_provider, list_providers
from mtp.cli.tui_provider_factory import ProviderSelection, build_tui_provider, normalize_tui_provider
from mtp.cli.tui_settings import DEFAULT_PROVIDER_MODELS
from mtp.providers import MockPlannerProvider
from mtp.toolkits import (
    CalculatorToolkit,
    Crawl4aiToolkit,
    FileToolkit,
    Newspaper4kToolkit,
    NewspaperToolkit,
    PythonToolkit,
    ShellToolkit,
    WebsiteToolkit,
    WikipediaToolkit,
)


PROVIDER_LABELS: dict[str, str] = {
    "mock": "Mock",
    "groq": "Groq",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "sambanova": "SambaNova",
    "cerebras": "Cerebras",
    "deepseek": "DeepSeek",
    "mistral": "Mistral",
    "cohere": "Cohere",
    "togetherai": "TogetherAI",
    "fireworksai": "FireworksAI",
    "xiaomi": "Xiaomi",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
}

DEFAULT_SHELL_COMMANDS = (
    "echo,pwd,ls,dir,cat,type,rg"
)

DEFAULT_ENABLED_TOOLKITS = ("calculator", "file")

TOOLKIT_LABELS: dict[str, str] = {
    "calculator": "Calculator",
    "file": "Files",
    "python": "Python",
    "shell": "Shell",
    "website": "Website",
    "wikipedia": "Wikipedia",
    "newspaper": "Newspaper3k",
    "newspaper4k": "Newspaper4k",
    "crawl4ai": "Crawl4AI",
}


@dataclass(frozen=True)
class AppConfig:
    provider_name: str
    model: str
    api_key: str
    base_url: str
    working_dir: str
    max_rounds: int
    system_instructions: str
    agent_instructions: str
    autoresearch: bool
    research_instructions: str
    strict_dependency_mode: bool
    debug_mode: bool
    stream_tool_events: bool
    stream_tool_results: bool
    enabled_toolkits: tuple[str, ...]
    shell_allowed_commands: str
    enable_calculator_member: bool


@dataclass
class RunViewState:
    final_text_chunks: list[str] = field(default_factory=list)
    thinking_text: str = ""
    saw_tool_round: bool = False
    tool_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_order: list[str] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    event_log: list[str] = field(default_factory=list)
    totals: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "llm_calls": 0,
        }
    )
    total_duration: float = 0.0
    started_at: float = field(default_factory=perf_counter)
    status: str = "Starting"


def _short(value: Any, limit: int = 220) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
        except Exception:
            text = repr(value)
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _merge_stream_text(existing: str, incoming: str) -> str:
    if not incoming:
        return existing
    if not existing:
        return incoming
    if incoming in existing:
        return existing
    if existing in incoming:
        return incoming

    max_overlap = min(len(existing), len(incoming), 4000)
    for size in range(max_overlap, 0, -1):
        if existing.endswith(incoming[:size]):
            return existing + incoming[size:]
    return existing + incoming


def _set_merged_chunks(chunks: list[str], incoming: str) -> None:
    merged = _merge_stream_text("".join(chunks), incoming)
    chunks[:] = [merged] if merged else []


def _prefer(existing: str | None, incoming: str | None) -> str | None:
    existing_text = existing.strip() if isinstance(existing, str) and existing.strip() else None
    incoming_text = incoming.strip() if isinstance(incoming, str) and incoming.strip() else None
    if incoming_text is None:
        return existing_text
    if existing_text is None:
        return incoming_text
    if len(incoming_text) > len(existing_text):
        return incoming_text
    return existing_text


def _launch_working_dir() -> Path:
    raw = os.environ.get("MTP_AGENT_OS_CWD") or os.environ.get("INIT_CWD") or os.getcwd()
    return Path(raw).expanduser().resolve()


def _provider_options(provider_name: str) -> dict[str, Any] | None:
    if provider_name != "xiaomi":
        return None
    return {"thinking_mode": "adaptive", "final_thinking_mode": "enabled"}


def _default_model(provider_name: str) -> str:
    normalized = "claude" if provider_name == "anthropic" else provider_name
    if provider_name == "mock":
        return "simple-planner"
    return DEFAULT_PROVIDER_MODELS.get(normalized, "gpt-4o")


def _provider_choices() -> list[str]:
    names = [info.name for info in list_providers()]
    return [name for name in names if name in PROVIDER_LABELS]


def _provider_caption(provider_name: str) -> str:
    info = get_provider(provider_name)
    if info is None:
        return ""
    bits = []
    if info.env_var:
        bits.append(f"env: {info.env_var}")
    if info.sdk_module:
        bits.append(f"sdk: {info.sdk_module}")
    if info.notes:
        bits.append(info.notes)
    return " | ".join(bits)


def _normalize_working_dir(path_text: str) -> Path:
    root = Path(path_text or ".").expanduser().resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise NotADirectoryError(f"Working directory is not a directory: {root}")
    return root


def _init_state() -> None:
    defaults = {
        "messages": [],
        "agent_key": None,
        "agent": None,
        "session_id": f"agent-os-{uuid4()}",
        "last_submit": None,
        "last_submit_at": 0.0,
        "active_run_id": None,
        "pending_prompt": None,
        "pending_turn_id": None,
        "completed_turn_ids": set(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _build_provider(config: AppConfig) -> Any:
    api_key = config.api_key.strip() or None
    base_url = config.base_url.strip() or None
    if config.provider_name == "mock":
        return MockPlannerProvider()

    provider_name = normalize_tui_provider(config.provider_name)
    return build_tui_provider(
        ProviderSelection(
            provider_name=provider_name,
            model_name=config.model,
            api_key=api_key,
            base_url=base_url,
            provider_options=_provider_options(provider_name),
        )
    )


def _register_toolkits(config: AppConfig, tools: Agent.ToolRegistry, root: Path) -> None:
    enabled = set(config.enabled_toolkits)
    if "calculator" in enabled:
        tools.register_toolkit_loader("calculator", CalculatorToolkit())
    if "file" in enabled:
        tools.register_toolkit_loader("file", FileToolkit(base_dir=root))
    if "python" in enabled:
        tools.register_toolkit_loader("python", PythonToolkit(base_dir=root))
    if "shell" in enabled:
        allowed = {
            item.strip().lower()
            for item in config.shell_allowed_commands.split(",")
            if item.strip()
        }
        tools.register_toolkit_loader(
            "shell",
            ShellToolkit(base_dir=root, allowed_commands=allowed or None),
        )
    if "website" in enabled:
        tools.register_toolkit_loader("website", WebsiteToolkit())
    if "wikipedia" in enabled:
        tools.register_toolkit_loader("wikipedia", WikipediaToolkit())
    if "newspaper" in enabled:
        tools.register_toolkit_loader("newspaper", NewspaperToolkit())
    if "newspaper4k" in enabled:
        tools.register_toolkit_loader("newspaper4k", Newspaper4kToolkit(include_summary=True))
    if "crawl4ai" in enabled:
        tools.register_toolkit_loader("crawl4ai", Crawl4aiToolkit(default_max_length=5000))


def _build_agent(config: AppConfig) -> Agent.MTPAgent:
    root = _normalize_working_dir(config.working_dir)
    os.chdir(root)

    provider = _build_provider(config)
    tools = Agent.ToolRegistry()
    _register_toolkits(config, tools, root)

    members: dict[str, Agent] = {}
    if config.enable_calculator_member:
        calculator_tools = Agent.ToolRegistry()
        calculator_tools.register_toolkit_loader("calculator", CalculatorToolkit())
        members["calculator"] = Agent(
            provider=_build_provider(config),
            tools=calculator_tools,
            mode="member",
            instructions="You are the calculator member agent. Solve math tasks precisely and return concise results.",
            debug_mode=config.debug_mode,
            strict_dependency_mode=config.strict_dependency_mode,
            system_instructions=config.system_instructions.strip() or None,
        )

    working_instruction = (
        f"Current working directory: {root}. Use this as the project root. "
        "When editing code, read first, write only inside this directory, and verify with available tools."
    )
    user_instructions = "\n\n".join(
        part.strip()
        for part in (working_instruction, config.agent_instructions)
        if part and part.strip()
    )

    return Agent.MTPAgent(
        provider=provider,
        tools=tools,
        mode="orchestration" if members else "standalone",
        members=members or None,
        instructions=user_instructions,
        autoresearch=config.autoresearch,
        research_instructions=config.research_instructions.strip() or None,
        debug_mode=config.debug_mode,
        stream_tool_events=config.stream_tool_events,
        stream_tool_results=config.stream_tool_results,
        strict_dependency_mode=config.strict_dependency_mode,
        system_instructions=config.system_instructions.strip() or None,
    )


def _agent_key(config: AppConfig) -> tuple[Any, ...]:
    return (
        config.provider_name,
        config.model,
        config.api_key,
        config.base_url,
        config.working_dir,
        config.system_instructions,
        config.agent_instructions,
        config.autoresearch,
        config.research_instructions,
        config.strict_dependency_mode,
        config.debug_mode,
        config.stream_tool_events,
        config.stream_tool_results,
        config.enabled_toolkits,
        config.shell_allowed_commands,
        config.enable_calculator_member,
    )


def _ensure_agent(config: AppConfig) -> Agent.MTPAgent:
    key = _agent_key(config)
    if st.session_state.agent is None or st.session_state.agent_key != key:
        st.session_state.agent = _build_agent(config)
        st.session_state.agent_key = key
    return st.session_state.agent


def _history_tool_line(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "done")
    name = str(item.get("name") or "tool")
    reasoning = item.get("reasoning")
    suffix = f": {_short(reasoning, 180)}" if isinstance(reasoning, str) and reasoning.strip() else ""
    return f"- `{status}` `{name}`{suffix}"


def _render_history() -> None:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            thinking = msg.get("thinking")
            if isinstance(thinking, str) and thinking.strip():
                with st.expander("Thinking", expanded=False):
                    st.markdown(thinking)
            tool_events = msg.get("tool_events")
            if isinstance(tool_events, list) and tool_events:
                with st.expander("Tool Calls", expanded=False):
                    st.markdown("\n".join(_history_tool_line(item) for item in tool_events))
            usage_lines = msg.get("usage_lines")
            if isinstance(usage_lines, list) and usage_lines:
                st.caption(" | ".join(str(line) for line in usage_lines))


def _render_tool_panel(state: RunViewState, placeholder: Any) -> None:
    if not state.tool_order:
        placeholder.markdown("_No tool calls yet._")
        return
    lines = []
    for key in state.tool_order:
        item = state.tool_state[key]
        status = str(item.get("status") or "queued")
        name = str(item.get("name") or "tool")
        reasoning = item.get("reasoning")
        line = f"- `{status}` `{name}`"
        if isinstance(reasoning, str) and reasoning.strip():
            line += f": {_short(reasoning, 160)}"
        if item.get("cached"):
            line += " (cached)"
        if item.get("error"):
            line += f" - {_short(item.get('error'), 140)}"
        lines.append(line)
    placeholder.markdown("\n".join(lines))


def _render_metrics(state: RunViewState, placeholder: Any) -> None:
    elapsed = max(perf_counter() - state.started_at, 0.0)
    totals = state.totals
    lines = [
        f"**Status:** {state.status}",
        (
            "**Tokens:** "
            f"{totals['input_tokens']} in / {totals['output_tokens']} out / "
            f"{totals['total_tokens']} total / {totals['reasoning_tokens']} thinking"
        ),
        f"**LLM calls:** {totals['llm_calls']}   **Elapsed:** {elapsed:.1f}s",
    ]
    cache_values = [
        totals["cached_input_tokens"],
        totals["cache_write_tokens"],
        totals["cache_creation_input_tokens"],
        totals["cache_read_input_tokens"],
    ]
    if any(cache_values):
        lines.append(
            "**Cache:** "
            f"{cache_values[0]} cached / {cache_values[1]} write / "
            f"{cache_values[2]} create / {cache_values[3]} read"
        )
    placeholder.markdown("\n\n".join(lines))


def _render_event_log(state: RunViewState, placeholder: Any) -> None:
    if not state.event_log:
        placeholder.markdown("_Waiting for events._")
        return
    placeholder.code("\n".join(state.event_log[-80:]), language="text")


def _append_event_log(state: RunViewState, event: dict[str, Any]) -> None:
    event_type = str(event.get("type") or "event")
    sequence = event.get("sequence")
    round_idx = event.get("round")
    bits = [event_type]
    if sequence is not None:
        bits.append(f"seq={sequence}")
    if round_idx is not None:
        bits.append(f"round={round_idx}")
    if event.get("tool_name"):
        bits.append(f"tool={event.get('tool_name')}")
    if event.get("stage"):
        bits.append(f"stage={event.get('stage')}")
    state.event_log.append(" | ".join(bits))


def _record_tool_event(state: RunViewState, key: str) -> None:
    item = state.tool_state[key]
    existing = next((event for event in state.tool_events if event.get("key") == key), None)
    payload = {
        "key": key,
        "call_id": item.get("call_id"),
        "name": item.get("name"),
        "status": item.get("status"),
        "reasoning": item.get("reasoning"),
        "cached": item.get("cached"),
        "error": item.get("error"),
    }
    if existing is None:
        state.tool_events.append(payload)
    else:
        existing.update(payload)


def _consume_event(state: RunViewState, event: dict[str, Any]) -> None:
    _append_event_log(state, event)
    event_type = str(event.get("type") or "")

    if event_type == "run_started":
        tools = int(event.get("tools_available") or 0)
        state.status = f"Run started with {tools} tools"
        return

    if event_type == "round_started":
        state.status = f"Round {event.get('round')} planning"
        return

    if event_type == "reasoning_chunk":
        state.thinking_text = _merge_stream_text(state.thinking_text, str(event.get("chunk") or ""))
        state.status = "Streaming thinking"
        return

    if event_type == "text_chunk":
        chunk = str(event.get("chunk") or "")
        source = str(event.get("source") or "")
        if source in {"finalize_stream", "finalize_fallback"} or not state.saw_tool_round:
            _set_merged_chunks(state.final_text_chunks, chunk)
        state.status = "Streaming answer"
        return

    if event_type == "llm_response":
        usage = event.get("usage")
        if isinstance(usage, dict):
            for key in state.totals:
                if key == "llm_calls":
                    continue
                value = usage.get(key)
                if isinstance(value, int):
                    state.totals[key] += value
            state.totals["llm_calls"] += 1
        reasoning = event.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            state.thinking_text = _merge_stream_text(state.thinking_text, reasoning)
        duration = event.get("duration_seconds")
        if isinstance(duration, int | float):
            state.total_duration += float(duration)
        stage = str(event.get("stage") or "planning")
        state.status = f"Model response received ({stage})"
        return

    if event_type in {"plan_received", "batch_started"}:
        state.saw_tool_round = True
        state.final_text_chunks = []
        if event_type == "plan_received":
            state.status = "Tool plan received"
        else:
            state.status = f"Tool batch {event.get('batch_index')} started"
        return

    if event_type == "tool_started":
        state.saw_tool_round = True
        state.final_text_chunks = []
        call_id = str(event.get("call_id") or "")
        tool_name = str(event.get("tool_name") or "tool")
        key = call_id or f"{tool_name}:{len(state.tool_order)}"
        reasoning = event.get("reasoning")
        reasoning_text = str(reasoning).strip() if isinstance(reasoning, str) and reasoning.strip() else None
        if key not in state.tool_state:
            state.tool_state[key] = {
                "key": key,
                "call_id": call_id,
                "name": tool_name,
                "status": "running",
                "reasoning": reasoning_text,
                "arguments": event.get("arguments"),
            }
            state.tool_order.append(key)
        else:
            current = state.tool_state[key]
            current["status"] = "running"
            current["reasoning"] = _prefer(current.get("reasoning"), reasoning_text)
            current["arguments"] = event.get("arguments") or current.get("arguments")
        _record_tool_event(state, key)
        state.status = f"Running {tool_name}"
        return

    if event_type == "tool_finished":
        call_id = str(event.get("call_id") or "")
        tool_name = str(event.get("tool_name") or "tool")
        key = call_id or next(
            (item_key for item_key in state.tool_order if state.tool_state[item_key].get("name") == tool_name),
            f"{tool_name}:{len(state.tool_order)}",
        )
        if key not in state.tool_state:
            state.tool_state[key] = {"key": key, "call_id": call_id, "name": tool_name}
            state.tool_order.append(key)
        item = state.tool_state[key]
        success = bool(event.get("success"))
        item["status"] = "done" if success else "failed"
        item["cached"] = event.get("cached")
        item["reasoning"] = _prefer(item.get("reasoning"), event.get("reasoning"))
        if not success:
            item["error"] = event.get("error") or "Tool failed"
        if event.get("output") is not None:
            item["output"] = _short(event.get("output"), 300)
        _record_tool_event(state, key)
        state.status = f"{tool_name} {'completed' if success else 'failed'}"
        return

    if event_type == "strict_violations":
        state.status = "Strict dependency violation; replanning"
        return

    if event_type == "tool_retry_requested":
        state.status = f"Tool retry requested: {event.get('tool_name')}"
        return

    if event_type == "run_completed":
        final_text = str(event.get("final_text") or "")
        current = "".join(state.final_text_chunks).strip()
        if final_text:
            if state.saw_tool_round or not current:
                state.final_text_chunks = [final_text]
            else:
                _set_merged_chunks(state.final_text_chunks, final_text)
        state.status = "Run completed"
        return

    if event_type == "run_failed":
        state.status = f"Run failed: {event.get('error') or 'unknown error'}"
        return

    if event_type == "run_cancelled":
        state.status = "Run cancelled"
        return


def _usage_lines(state: RunViewState) -> list[str]:
    totals = state.totals
    elapsed = max(perf_counter() - state.started_at, state.total_duration, 0.0)
    lines = [
        "tokens(in/out/total/reasoning)="
        f"{totals['input_tokens']}/"
        f"{totals['output_tokens']}/"
        f"{totals['total_tokens']}/"
        f"{totals['reasoning_tokens']}",
        f"llm_calls={totals['llm_calls']}",
        f"duration={elapsed:.2f}s",
    ]
    if state.thinking_text.strip():
        lines.append(f"thinking={_short(state.thinking_text, 260)}")
    return lines


def _run_turn(agent: Agent.MTPAgent, prompt: str, config: AppConfig) -> tuple[str, str, list[dict[str, Any]], list[str]]:
    state = RunViewState()
    run_id = f"agent-os-run-{uuid4()}"
    st.session_state.active_run_id = run_id

    with st.chat_message("assistant"):
        status_ph = st.empty()
        metrics_ph = st.empty()
        response_ph = st.empty()
        with st.expander("Thinking", expanded=True):
            thinking_ph = st.empty()
        with st.expander("Tool Calls", expanded=True):
            tool_ph = st.empty()
        with st.expander("Event Stream", expanded=False):
            event_ph = st.empty()

        status_ph.info("Starting agent run...")
        response_ph.markdown("_Waiting for the first token..._")
        thinking_ph.markdown("_No thinking tokens yet._")
        tool_ph.markdown("_No tool calls yet._")
        _render_metrics(state, metrics_ph)

        for event in agent.run_events(
            prompt,
            max_rounds=config.max_rounds,
            session_id=st.session_state.session_id,
            stream_final=True,
            stream_tool_events=config.stream_tool_events,
            stream_tool_results=config.stream_tool_results,
            run_id=run_id,
            metadata={"working_dir": config.working_dir, "ui": "streamlit-agent-os"},
        ):
            _consume_event(state, event)
            final_text = "".join(state.final_text_chunks)
            if final_text.strip():
                response_ph.markdown(final_text)
            elif state.saw_tool_round:
                response_ph.markdown("_Using tools before writing the final answer..._")

            if state.thinking_text.strip():
                thinking_ph.markdown(state.thinking_text)
            _render_tool_panel(state, tool_ph)
            _render_metrics(state, metrics_ph)
            _render_event_log(state, event_ph)

            if state.status == "Run completed":
                status_ph.success(state.status)
            elif state.status.startswith("Run failed"):
                status_ph.error(state.status)
            else:
                status_ph.info(state.status)

    st.session_state.active_run_id = None
    final_text = "".join(state.final_text_chunks).strip() or "_No final text returned._"
    return final_text, state.thinking_text.strip(), state.tool_events, _usage_lines(state)


def _tool_multiselect(default_enabled: list[str]) -> tuple[str, ...]:
    options = list(TOOLKIT_LABELS)
    selected = st.multiselect(
        "Enabled Toolkits",
        options=options,
        default=default_enabled,
        format_func=lambda item: TOOLKIT_LABELS[item],
        help="All tools are scoped to the working directory where applicable.",
    )
    return tuple(selected)


def _sidebar_config() -> AppConfig:
    launch_dir = _launch_working_dir()
    provider_options = _provider_choices()
    with st.sidebar:
        st.header("Agent")
        provider_name = st.selectbox(
            "Provider",
            provider_options,
            index=provider_options.index("groq") if "groq" in provider_options else 0,
            format_func=lambda item: PROVIDER_LABELS.get(item, item),
        )
        caption = _provider_caption(provider_name)
        if caption:
            st.caption(caption)

        default_model = _default_model(provider_name)
        model = st.text_input("Model", value=default_model)
        api_key = st.text_input("API Key", value="", type="password", help="Optional; falls back to provider env vars.")

        base_url_default = ""
        if provider_name == "ollama":
            base_url_default = "http://localhost:11434"
        elif provider_name == "lmstudio":
            base_url_default = "http://127.0.0.1:1234/v1"
        elif provider_name == "xiaomi":
            base_url_default = "https://token-plan-ams.xiaomimimo.com/v1"
        base_url = st.text_input("Base URL", value=base_url_default)

        working_dir = st.text_input(
            "Working Directory",
            value=str(launch_dir),
            help="Defaults to the folder where `mtp agent-os` was launched.",
        )
        max_rounds = st.slider("Max Rounds", min_value=1, max_value=100, value=8, step=1)

        st.subheader("Tools")
        enabled_toolkits = _tool_multiselect(default_enabled=list(DEFAULT_ENABLED_TOOLKITS))
        shell_allowed_commands = st.text_input(
            "Shell Allowed Commands",
            value=DEFAULT_SHELL_COMMANDS,
            disabled="shell" not in enabled_toolkits,
        )
        enable_calculator_member = st.checkbox("Calculator Sub-agent", value=True)
        if enabled_toolkits:
            st.caption(f"{len(enabled_toolkits)} toolkit(s) selected.")

        st.subheader("Instructions")
        system_instructions = st.text_area(
            "System Instructions",
            value=(
                "You are operating under MTP Agent OS. Reason step by step for longer tasks, "
                "use tools when they improve correctness, and report concise final results."
            ),
            height=110,
        )
        agent_instructions = st.text_area(
            "Agent Instructions",
            value=(
                "Work like a coding agent inside the selected working directory. Inspect the codebase, "
                "make focused edits with file tools, and verify changes with available commands."
            ),
            height=110,
        )
        autoresearch = st.checkbox("Persistent Autoresearch Mode", value=False)
        research_instructions = st.text_area(
            "Autoresearch Instructions",
            value=(
                "Stay in persistent work mode until the user's request is genuinely complete. "
                "Avoid loops: if the same tool result repeats, synthesize an answer or change strategy."
            ),
            height=90,
            disabled=not autoresearch,
        )

        st.subheader("Streaming")
        strict_dependency_mode = st.checkbox("Strict Dependency Mode", value=True)
        debug_mode = st.checkbox("Debug Mode", value=False)
        stream_tool_events = st.checkbox("Show Tool Events", value=True)
        stream_tool_results = st.checkbox("Show Tool Results", value=False, disabled=not stream_tool_events)

        cols = st.columns(2)
        with cols[0]:
            if st.button("Clear Chat", use_container_width=True):
                st.session_state.messages = []
                st.session_state.session_id = f"agent-os-{uuid4()}"
                st.session_state.last_submit = None
                st.session_state.pending_prompt = None
                st.session_state.pending_turn_id = None
                st.session_state.active_run_id = None
                st.session_state.completed_turn_ids = set()
                st.rerun()
        with cols[1]:
            if st.button("Rebuild Agent", use_container_width=True):
                st.session_state.agent = None
                st.session_state.agent_key = None
                st.rerun()

    return AppConfig(
        provider_name=provider_name,
        model=model.strip(),
        api_key=api_key,
        base_url=base_url.strip(),
        working_dir=working_dir.strip(),
        max_rounds=int(max_rounds),
        system_instructions=system_instructions,
        agent_instructions=agent_instructions,
        autoresearch=bool(autoresearch),
        research_instructions=research_instructions,
        strict_dependency_mode=bool(strict_dependency_mode),
        debug_mode=bool(debug_mode),
        stream_tool_events=bool(stream_tool_events),
        stream_tool_results=bool(stream_tool_results) if stream_tool_events else False,
        enabled_toolkits=enabled_toolkits,
        shell_allowed_commands=shell_allowed_commands,
        enable_calculator_member=bool(enable_calculator_member),
    )


def _apply_style() -> None:
    st.markdown(
        """
        <style>
        .block-container { max-width: 1180px; padding-top: 1.4rem; }
        [data-testid="stSidebar"] .stTextInput input,
        [data-testid="stSidebar"] textarea { font-size: 0.9rem; }
        div[data-testid="stExpander"] { border-radius: 8px; }
        .mtp-status-strip {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.75rem;
            margin: 0.75rem 0 1rem 0;
        }
        .mtp-status-strip div {
            border: 1px solid rgba(128, 128, 128, 0.25);
            border-radius: 8px;
            padding: 0.65rem 0.75rem;
        }
        .mtp-status-strip span {
            display: block;
            opacity: 0.68;
            font-size: 0.76rem;
        }
        .mtp-status-strip strong {
            display: block;
            font-size: 0.92rem;
            overflow-wrap: anywhere;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _is_duplicate_submit(prompt: str) -> bool:
    now = monotonic()
    previous = st.session_state.get("last_submit")
    previous_at = float(st.session_state.get("last_submit_at") or 0.0)
    if previous == prompt and now - previous_at < 1.5:
        return True
    st.session_state.last_submit = prompt
    st.session_state.last_submit_at = now
    return False


def main() -> None:
    Agent.load_dotenv_if_available()
    st.set_page_config(page_title="MTP Agent OS", page_icon="MTP", layout="wide")
    _apply_style()
    _init_state()

    st.title("MTP Agent OS")
    st.caption("A Streamlit control room for MTP agents, live reasoning, tool calls, and coding workspaces.")

    config = _sidebar_config()
    if not config.model:
        st.error("Model name is required.")
        return

    try:
        working_dir = _normalize_working_dir(config.working_dir)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Working directory error: {exc}")
        return

    st.markdown(
        f"""
        <div class="mtp-status-strip">
            <div><span>Working directory</span><strong>{escape(str(working_dir))}</strong></div>
            <div><span>Provider</span><strong>{escape(PROVIDER_LABELS.get(config.provider_name, config.provider_name))} / {escape(config.model)}</strong></div>
            <div><span>Tools</span><strong>{len(config.enabled_toolkits)} toolkits enabled</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not config.enabled_toolkits and not config.enable_calculator_member:
        st.warning("No tools are enabled. Enable at least one toolkit for codebase work.")

    try:
        agent = _ensure_agent(config)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to initialize agent: {exc}")
        return

    _render_history()
    disabled = st.session_state.active_run_id is not None or st.session_state.get("pending_prompt") is not None
    prompt = st.chat_input("Ask the agent to inspect, edit, run, or explain this codebase...", disabled=disabled)
    if prompt:
        if _is_duplicate_submit(prompt):
            st.info("Skipped a duplicate submit from the last rerun.")
            return
        turn_id = f"turn-{uuid4()}"
        st.session_state.pending_prompt = prompt
        st.session_state.pending_turn_id = turn_id
        st.session_state.messages.append({"role": "user", "content": prompt, "turn_id": turn_id})
        st.rerun()

    pending_prompt = st.session_state.get("pending_prompt")
    pending_turn_id = st.session_state.get("pending_turn_id")
    completed_turn_ids = st.session_state.get("completed_turn_ids")
    if not isinstance(completed_turn_ids, set):
        completed_turn_ids = set(completed_turn_ids or [])
        st.session_state.completed_turn_ids = completed_turn_ids

    if not isinstance(pending_prompt, str) or not pending_prompt.strip():
        return
    if not isinstance(pending_turn_id, str) or pending_turn_id in completed_turn_ids:
        st.session_state.pending_prompt = None
        st.session_state.pending_turn_id = None
        return
    if st.session_state.active_run_id is not None:
        st.info("A run is already active. Waiting for it to finish before accepting another prompt.")
        return

    st.session_state.pending_prompt = None
    st.session_state.pending_turn_id = None
    st.session_state.active_run_id = pending_turn_id

    try:
        final_text, thinking_text, tool_events, usage_lines = _run_turn(agent, pending_prompt, config)
    except Exception as exc:  # noqa: BLE001
        st.session_state.active_run_id = None
        with st.chat_message("assistant"):
            st.error(f"Run crashed: {exc}")
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": f"Run crashed: {exc}",
                "turn_id": pending_turn_id,
                "tool_events": [],
                "usage_lines": ["tokens(in/out/total/reasoning)=error/error/error/error"],
            }
        )
        completed_turn_ids.add(pending_turn_id)
        st.session_state.completed_turn_ids = completed_turn_ids
        return

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": final_text,
            "turn_id": pending_turn_id,
            "thinking": thinking_text,
            "tool_events": tool_events,
            "usage_lines": usage_lines,
        }
    )
    completed_turn_ids.add(pending_turn_id)
    st.session_state.completed_turn_ids = completed_turn_ids
    st.session_state.active_run_id = None


if __name__ == "__main__":
    main()
