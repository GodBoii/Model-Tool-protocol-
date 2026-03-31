from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, ToolRegistry, load_dotenv_if_available, register_local_toolkits
from mtp.providers import GroqToolCallingProvider

def main() -> None:
    # Provider-agnostic env loading for all future adapters (Groq/OpenAI/Claude/Gemini/etc.).
    load_dotenv_if_available()

    registry = ToolRegistry()
    register_local_toolkits(registry, base_dir=pathlib.Path.cwd())

    provider = GroqToolCallingProvider(
        model="moonshotai/kimi-k2-instruct",
        system_prompt=(
            "You are an agent that uses tools when needed. "
            "Available local toolkits include calculator, file, python, and shell. "
            "Use tools for concrete computation or file operations."
        ),
    )
    agent = Agent(provider=provider, registry=registry, debug_mode=True)
    reply = agent.run_loop(
        "Calculate (25 * 4) + 10 and then list files in the current directory. "
        "Give a short summary.",
        max_rounds=4,
    )
    print(reply)


if __name__ == "__main__":
    main()
