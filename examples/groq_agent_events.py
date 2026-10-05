from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent
from mtp.providers import Groq
from mtp.toolkits import CalculatorToolkit, FileToolkit, PythonToolkit, ShellToolkit


def main() -> None:
    Agent.load_dotenv_if_available()

    calculator_tools = Agent.ToolRegistry()
    calculator_tools.register_toolkit_loader("calculator", CalculatorToolkit())

    tools = Agent.ToolRegistry()
    tools.register_toolkit_loader("file", FileToolkit(base_dir=pathlib.Path.cwd()))
    tools.register_toolkit_loader("python", PythonToolkit(base_dir=pathlib.Path.cwd()))
    tools.register_toolkit_loader("shell", ShellToolkit(base_dir=pathlib.Path.cwd()))

    provider = Groq(
        model="qwen/qwen3.8-27b",
        strict_dependency_mode=True,
    )
    calculator_agent = Agent(
        provider=provider,
        tools=calculator_tools,
        mode="member",
        instructions="You are the calculator member agent. Solve math tasks precisely and return concise results.",
        debug_mode=True,
        strict_dependency_mode=True,
    )

    agent = Agent.MTPAgent(
        provider=provider,
        tools=tools,
        mode="orchestration",
        members={"calculator": calculator_agent},
        instructions=(
            "You are the orchestrator agent. Delegate math to agent.member.calculator, "
            "use tools for file/system operations, and be concise."
        ),
        autoresearch=True,
        research_instructions=(
            "Stay in persistent work mode until the request is fully complete. "
            "Do not stop after a plausible answer. Verify results with tools when useful, "
            "and call agent.terminate only after you have finished the task."
        ),
        debug_mode=True,
        stream_tool_events=True,
        stream_tool_results=False,
        strict_dependency_mode=True,
    )

    prompt = (
        "Calculate (25 * 4) + 10 and then list files in the current directory. "
        "Give a short summary. When you are fully done, call agent.terminate with the completion reason "
        "and the final summary."
    )

    agent.print_response(
        prompt,
        max_rounds=12,
        stream=True,
        stream_events=True,
    )


if __name__ == "__main__":
    main()
