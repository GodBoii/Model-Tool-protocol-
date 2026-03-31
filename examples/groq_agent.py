from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import MTPAgent, ToolRegistry, load_dotenv_if_available
from mtp.providers import Groq
from mtp.toolkits import CalculatorToolkit, FileToolkit, PythonToolkit, ShellToolkit

def main() -> None:
    load_dotenv_if_available()

    registry = ToolRegistry()
    registry.register_toolkit_loader("calculator", CalculatorToolkit())
    registry.register_toolkit_loader("file", FileToolkit(base_dir=pathlib.Path.cwd()))
    registry.register_toolkit_loader("python", PythonToolkit(base_dir=pathlib.Path.cwd()))
    registry.register_toolkit_loader("shell", ShellToolkit(base_dir=pathlib.Path.cwd()))

    provider = Groq(
        model="moonshotai/kimi-k2-instruct",
        strict_dependency_mode=True,
    )

    agent = MTPAgent(
        provider=provider,
        registry=registry,
        instructions="Use tools for concrete computation or file operations and be concise.",
        debug_mode=True,
        strict_dependency_mode=True,
    )
    reply = agent.run(
        "Calculate (25 * 4) + 10 and then list files in the current directory.  "
        "Give a short summary.",
        max_rounds=4,
    )
    print(reply)


if __name__ == "__main__":
    main()
