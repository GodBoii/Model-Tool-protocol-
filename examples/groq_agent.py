from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent
from mtp.providers import Groq
from mtp.toolkits import CalculatorToolkit, FileToolkit, PythonToolkit, ShellToolkit


_retry_state = {"count": 0}


@Agent.mtp_tool(name="demo.retry_once", description="Retry once, then succeed.")
def retry_once(task: str = "") -> str:
    _ = task
    _retry_state["count"] += 1
    if _retry_state["count"] == 1:
        raise Agent.RetryAgentRun("Please call demo.retry_once one more time with the same task.")
    return "retry_succeeded"


@Agent.mtp_tool(name="demo.stop_now", description="Stop/pause the current run.")
def stop_now(reason: str = "manual-approval-required") -> str:
    raise Agent.StopAgentRun(f"Run paused by tool: {reason}")


@Agent.mtp_tool(name="demo.echo", description="Echo text back.")
def echo(text: str) -> str:
    return text


def section(title: str) -> None:
    print(f"\n{'=' * 20} {title} {'=' * 20}")


def safe_print_json(name: str, value: Any) -> None:
    try:
        print(f"{name}: {json.dumps(value, indent=2, default=str)}")
    except Exception:
        print(f"{name}: {value}")


def main() -> None:
    Agent.load_dotenv_if_available()

    tools = Agent.ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())
    tools.register_toolkit_loader("file", FileToolkit(base_dir=pathlib.Path.cwd()))
    tools.register_toolkit_loader("python", PythonToolkit(base_dir=pathlib.Path.cwd()))
    tools.register_toolkit_loader("shell", ShellToolkit(base_dir=pathlib.Path.cwd()))

    # Main provider with all exposed constructor controls.
    provider = Groq(
        model="llama-3.3-70b-versatile",
        system_prompt="You are a precise tool-using assistant. Prefer explicit tool calls when needed.",
        temperature=0.0,
        tool_choice="auto",
        parallel_tool_calls=True,
        encourage_batch_tool_calls=True,
        strict_dependency_mode=True,
    )

    # Optional output refinement providers (same backend, independent settings).
    output_model = Groq(
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        tool_choice="auto",
        parallel_tool_calls=False,
        encourage_batch_tool_calls=False,
        strict_dependency_mode=False,
    )
    parser_model = Groq(
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        tool_choice="auto",
        parallel_tool_calls=False,
        encourage_batch_tool_calls=False,
        strict_dependency_mode=False,
    )

    agent = Agent(
        provider=provider,
        tools=tools,
        instructions=(
            "Use tools for concrete computation or file operations. "
            "When asked for JSON, return strict JSON only."
        ),
        debug_mode=True,
        strict_dependency_mode=True,
        stream_chunk_size=60,
        max_history_messages=300,
    )

    section("1) Baseline Multi-Tool Run")
    reply = agent.run_loop(
        "Calculate (25 * 4) + 10 and list files in the current directory. Give a short summary.",
        max_rounds=4,
        tool_call_limit=12,
    )
    print(reply)

    section("2) Structured Input + Structured Output + Output Model Pipeline")
    structured = agent.run_output(
        user_input={"a": 25, "b": 4, "c": 10, "task": "compute"},
        input_schema={
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
                "c": {"type": "integer"},
                "task": {"type": "string"},
            },
            "required": ["a", "b", "c", "task"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "answer": {"type": "integer"},
                "explanation": {"type": "string"},
            },
            "required": ["answer", "explanation"],
            "additionalProperties": False,
        },
        output_model=output_model,
        output_model_prompt=(
            "Rewrite the response to be concise and clean while preserving factual correctness."
        ),
        parser_model=parser_model,
        parser_model_prompt=(
            "Return strict JSON with keys: answer (integer), explanation (string). "
            "Do not include markdown."
        ),
        max_rounds=4,
        tool_call_limit=12,
    )
    print("final_text:", structured.final_text)
    safe_print_json("parsed_output", structured.output)
    print("output_validation_error:", structured.output_validation_error)

    section("3) add_tool() + RetryAgentRun")
    agent.add_tool(retry_once)
    retry_result = agent.run_output(
        "Call demo.retry_once tool. If it asks for retry, call it again with the same task.",
        max_rounds=4,
        tool_call_limit=8,
    )
    print("final_text:", retry_result.final_text)
    print("paused:", retry_result.paused)
    print("total_tool_calls:", retry_result.total_tool_calls)

    section("4) add_tool() + StopAgentRun + continue_run()")
    agent.add_tool(stop_now)
    paused = agent.run_output(
        "Call demo.stop_now with reason 'approve-manually' and then stop.",
        max_rounds=3,
        tool_call_limit=6,
    )
    print("paused:", paused.paused)
    print("pause_reason:", paused.pause_reason)
    if paused.paused:
        resumed = agent.continue_run(run_output=paused, max_rounds=3, tool_call_limit=6)
        print("continued_final_text:", resumed.final_text)
        print("continued_paused:", resumed.paused)

    section("5) set_tools() Replacement Test")
    agent.set_tools([echo])
    echo_run = agent.run_output(
        "Use demo.echo with text='set_tools worked'. Then answer in one short sentence.",
        max_rounds=3,
        tool_call_limit=4,
    )
    print("final_text:", echo_run.final_text)

    section("6) Event Stream Smoke Test")
    for event in agent.run_loop_events(
        "Use demo.echo with text='event stream test' and answer briefly.",
        max_rounds=2,
        stream_final=True,
        tool_call_limit=4,
    ):
        if event["type"] in {"run_started", "plan_received", "tool_started", "tool_finished", "run_paused", "run_completed"}:
            safe_print_json("event", event)


if __name__ == "__main__":
    main()

