from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import streamlit as st

from mtp import Agent
from mtp.providers import Groq, OpenAI, OpenRouter
from mtp.toolkits import CalculatorToolkit, FileToolkit, PythonToolkit, ShellToolkit


@dataclass(frozen=True)
class AppConfig:
    provider_name: str
    model: str
    api_key: str
    base_dir: str
    max_rounds: int
    system_instructions: str
    agent_instructions: str
    autoresearch: bool
    research_instructions: str
    strict_dependency_mode: bool
    debug_mode: bool
    stream_tool_events: bool
    stream_tool_results: bool
    enable_file_tools: bool
    enable_python_tools: bool
    enable_shell_tools: bool
    enable_calculator_member: bool


def _short(text: str, limit: int = 220) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


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


def _init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "agent_key" not in st.session_state:
        st.session_state.agent_key = None
    if "agent" not in st.session_state:
        st.session_state.agent = None
    if "session_id" not in st.session_state:
        st.session_state.session_id = f"agent-os-{uuid4()}"


def _apply_api_key(provider_name: str, api_key: str) -> None:
    key = api_key.strip()
    if not key:
        return
    if provider_name == "Groq":
        os.environ["GROQ_API_KEY"] = key
    elif provider_name == "OpenAI":
        os.environ["OPENAI_API_KEY"] = key
    elif provider_name == "OpenRouter":
        os.environ["OPENROUTER_API_KEY"] = key


def _provider_for_config(config: AppConfig):
    if config.provider_name == "Groq":
        return Groq(model=config.model, strict_dependency_mode=config.strict_dependency_mode)
    if config.provider_name == "OpenAI":
        return OpenAI(model=config.model, strict_dependency_mode=config.strict_dependency_mode)
    if config.provider_name == "OpenRouter":
        return OpenRouter(model=config.model, strict_dependency_mode=config.strict_dependency_mode)
    raise ValueError(f"Unsupported provider: {config.provider_name}")


def _build_agent(config: AppConfig) -> Agent.MTPAgent:
    _apply_api_key(config.provider_name, config.api_key)
    base_dir = Path(config.base_dir).expanduser().resolve()

    provider = _provider_for_config(config)
    tools = Agent.ToolRegistry()
    if config.enable_file_tools:
        tools.register_toolkit_loader("file", FileToolkit(base_dir=base_dir))
    if config.enable_python_tools:
        tools.register_toolkit_loader("python", PythonToolkit(base_dir=base_dir))
    if config.enable_shell_tools:
        tools.register_toolkit_loader("shell", ShellToolkit(base_dir=base_dir))

    members: dict[str, Agent] = {}
    if config.enable_calculator_member:
        calculator_tools = Agent.ToolRegistry()
        calculator_tools.register_toolkit_loader("calculator", CalculatorToolkit())
        calc_provider = _provider_for_config(config)
        members["calculator"] = Agent(
            provider=calc_provider,
            tools=calculator_tools,
            mode="member",
            instructions="You are the calculator member agent. Solve math tasks precisely and return concise results.",
            debug_mode=config.debug_mode,
            strict_dependency_mode=config.strict_dependency_mode,
            system_instructions=config.system_instructions.strip() or None,
        )

    return Agent.MTPAgent(
        provider=provider,
        tools=tools,
        mode="orchestration",
        members=members or None,
        instructions=config.agent_instructions.strip() or None,
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
        config.base_dir,
        config.system_instructions,
        config.agent_instructions,
        config.autoresearch,
        config.research_instructions,
        config.strict_dependency_mode,
        config.debug_mode,
        config.stream_tool_events,
        config.stream_tool_results,
        config.enable_file_tools,
        config.enable_python_tools,
        config.enable_shell_tools,
        config.enable_calculator_member,
    )


def _ensure_agent(config: AppConfig) -> Agent.MTPAgent:
    key = _agent_key(config)
    if st.session_state.agent is None or st.session_state.agent_key != key:
        st.session_state.agent = _build_agent(config)
        st.session_state.agent_key = key
    return st.session_state.agent


def _render_history() -> None:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            tool_events = msg.get("tool_events")
            if isinstance(tool_events, list) and tool_events:
                with st.expander("Tool Activity", expanded=False):
                    for item in tool_events:
                        name = str(item.get("name") or "tool")
                        reasoning = item.get("reasoning")
                        if isinstance(reasoning, str) and reasoning.strip():
                            st.markdown(f"- `{name}`: {reasoning}")
                        else:
                            st.markdown(f"- `{name}`")
            usage_lines = msg.get("usage_lines")
            if isinstance(usage_lines, list) and usage_lines:
                st.caption(" | ".join(str(line) for line in usage_lines))


def _run_turn(agent: Agent.MTPAgent, prompt: str, config: AppConfig) -> tuple[str, list[dict[str, str | None]], list[str]]:
    response_chunks: list[str] = []
    tool_state: dict[str, dict[str, str | None]] = {}
    tool_order: list[str] = []
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0}

    with st.chat_message("assistant"):
        status_ph = st.empty()
        tool_ph = st.empty()
        response_ph = st.empty()
        status_ph.info("Running agent...")
        tool_ph.markdown("_No tool events yet._")

        for event in agent.run_events(
            prompt,
            max_rounds=config.max_rounds,
            session_id=st.session_state.session_id,
            stream_final=True,
            stream_tool_events=config.stream_tool_events,
            stream_tool_results=config.stream_tool_results,
        ):
            event_type = str(event.get("type") or "")
            if event_type == "text_chunk":
                chunk = str(event.get("chunk") or "")
                if chunk:
                    response_chunks.append(chunk)
                    response_ph.markdown("".join(response_chunks))
                continue

            if event_type == "llm_response":
                usage = event.get("usage")
                if isinstance(usage, dict):
                    for metric_key in totals:
                        value = usage.get(metric_key)
                        if isinstance(value, int):
                            totals[metric_key] += value
                stage = str(event.get("stage") or "next_action")
                status_ph.info(f"Model responded ({stage})")
                continue

            if event_type == "tool_started":
                call_id = str(event.get("call_id") or "")
                tool_name = str(event.get("tool_name") or "tool")
                reasoning = event.get("reasoning")
                reasoning_text = str(reasoning).strip() if isinstance(reasoning, str) and reasoning.strip() else None
                key = call_id or f"{tool_name}:{len(tool_order)}"
                if key not in tool_state:
                    tool_state[key] = {"name": tool_name, "reasoning": reasoning_text}
                    tool_order.append(key)
                else:
                    current = tool_state[key]
                    current["reasoning"] = _prefer(current.get("reasoning"), reasoning_text)
                preview_lines: list[str] = []
                for item_key in tool_order:
                    item = tool_state[item_key]
                    item_name = str(item.get("name") or "tool")
                    item_reason = item.get("reasoning")
                    if isinstance(item_reason, str) and item_reason.strip():
                        preview_lines.append(f"- `{item_name}`: {_short(item_reason, 180)}")
                    else:
                        preview_lines.append(f"- `{item_name}`")
                tool_ph.markdown("\n".join(preview_lines) if preview_lines else "_No tool events yet._")
                continue

            if event_type == "run_completed":
                final_text = str(event.get("final_text") or "").strip()
                if final_text:
                    response_chunks = [final_text]
                    response_ph.markdown(final_text)
                status_ph.success("Run completed")
                continue

            if event_type == "run_failed":
                err = str(event.get("error") or "Unknown error")
                status_ph.error(f"Run failed: {err}")

    final_text = "".join(response_chunks).strip() or "_No final text returned._"
    tool_events = [tool_state[key] for key in tool_order]
    for item in tool_events:
        reasoning = item.get("reasoning")
        if isinstance(reasoning, str):
            item["reasoning"] = _short(reasoning, 220)
    usage_lines = [
        "tokens(in/out/total/reasoning)="
        f"{totals['input_tokens']}/"
        f"{totals['output_tokens']}/"
        f"{totals['total_tokens']}/"
        f"{totals['reasoning_tokens']}"
    ]
    return final_text, tool_events, usage_lines


def _sidebar_config() -> AppConfig:
    with st.sidebar:
        st.header("Agent Config")
        provider_name = st.selectbox("Provider", ["Groq", "OpenAI", "OpenRouter"], index=0)
        default_model = {
            "Groq": "moonshotai/kimi-k2-instruct",
            "OpenAI": "gpt-5.4-mini",
            "OpenRouter": "openai/gpt-5.4-mini",
        }[provider_name]
        model = st.text_input("Model", value=default_model)
        api_key = st.text_input("API Key", value="", type="password", help="Stored only in this session.")
        base_dir = st.text_input("Tools Base Directory", value=str(Path.cwd()))
        max_rounds = st.slider("Max Rounds", min_value=1, max_value=24, value=12, step=1)

        st.subheader("Instructions")
        system_instructions = st.text_area(
            "System Instructions",
            value=(
                "You are operating under MTP (Model Tool Protocol). When tools are needed, "
                "prefer complete and efficient planning."
            ),
            height=120,
        )
        agent_instructions = st.text_area(
            "Agent Instructions",
            value=(
                "You are the orchestrator agent. Delegate math to agent.member.calculator, "
                "use tools for file/system operations, and be concise."
            ),
            height=120,
        )
        autoresearch = st.checkbox("Enable Autoresearch", value=True)
        research_instructions = st.text_area(
            "Research Instructions",
            value=(
                "Stay in persistent work mode until the request is fully complete. "
                "Do not stop after a plausible answer. Verify results with tools when useful."
            ),
            height=100,
            disabled=not autoresearch,
        )

        st.subheader("Tooling")
        enable_file_tools = st.checkbox("Enable File Tools", value=True)
        enable_python_tools = st.checkbox("Enable Python Tools", value=True)
        enable_shell_tools = st.checkbox("Enable Shell Tools", value=True)
        enable_calculator_member = st.checkbox("Enable Calculator Member", value=True)

        st.subheader("Runtime")
        strict_dependency_mode = st.checkbox("Strict Dependency Mode", value=True)
        debug_mode = st.checkbox("Debug Mode", value=False)
        stream_tool_events = st.checkbox("Stream Tool Events", value=True)
        stream_tool_results = st.checkbox("Stream Tool Results", value=False, disabled=not stream_tool_events)

        if st.button("Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.session_id = f"agent-os-{uuid4()}"
            st.rerun()

    return AppConfig(
        provider_name=provider_name,
        model=model.strip(),
        api_key=api_key,
        base_dir=base_dir.strip(),
        max_rounds=int(max_rounds),
        system_instructions=system_instructions,
        agent_instructions=agent_instructions,
        autoresearch=bool(autoresearch),
        research_instructions=research_instructions,
        strict_dependency_mode=bool(strict_dependency_mode),
        debug_mode=bool(debug_mode),
        stream_tool_events=bool(stream_tool_events),
        stream_tool_results=bool(stream_tool_results) if stream_tool_events else False,
        enable_file_tools=bool(enable_file_tools),
        enable_python_tools=bool(enable_python_tools),
        enable_shell_tools=bool(enable_shell_tools),
        enable_calculator_member=bool(enable_calculator_member),
    )


def main() -> None:
    Agent.load_dotenv_if_available()
    st.set_page_config(page_title="MTP Agent OS", page_icon="MTP", layout="wide")
    _init_state()

    st.title("MTP Agent OS")
    st.caption("Interactive test bench for MTP agents with live tool activity.")

    config = _sidebar_config()
    if not config.model:
        st.error("Model name is required.")
        return
    if not any((config.enable_file_tools, config.enable_python_tools, config.enable_shell_tools)):
        st.warning("No local toolkits enabled. Enable at least one toolkit to run tool-based tasks.")

    try:
        agent = _ensure_agent(config)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to initialize agent: {exc}")
        return

    _render_history()
    prompt = st.chat_input("Ask something that may require tools...")
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    try:
        final_text, tool_events, usage_lines = _run_turn(agent, prompt, config)
    except Exception as exc:  # noqa: BLE001
        with st.chat_message("assistant"):
            st.error(f"Run crashed: {exc}")
        return

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": final_text,
            "tool_events": tool_events,
            "usage_lines": usage_lines,
        }
    )


if __name__ == "__main__":
    main()
