from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import MTPAgent, ToolRegistry, load_dotenv_if_available, mtp_tool, toolkit_from_functions
from mtp.providers import Groq


@mtp_tool(description="Add two integers and return the sum.")
def add(a: int, b: int) -> int:
    return a + b


@mtp_tool(description="Reverse a string.")
def reverse_text(text: str) -> str:
    return text[::-1]


def main() -> None:
    load_dotenv_if_available()
    provider = Groq(model="moonshotai/kimi-k2-instruct")

    registry = ToolRegistry()
    registry.register_toolkit_loader("custom", toolkit_from_functions("custom", add, reverse_text))

    agent = MTPAgent(
        provider=provider,
        registry=registry,
        instructions="Use tools when useful and keep responses concise.",
    )
    print(agent.run("Add 40 and 2, then reverse 'MTP'."))


if __name__ == "__main__":
    main()
