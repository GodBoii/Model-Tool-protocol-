from __future__ import annotations

from mtp import Agent, ToolRegistry, mtp_tool
from mtp.providers import Xiaomi
from mtp.protocol import ToolSpec


@mtp_tool("math.add", description="Add two numbers")
def add(a: float, b: float) -> float:
    return a + b


if __name__ == "__main__":
    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec(name="math.add", description="Add two numbers"),
        add,
    )

    provider = Xiaomi(model="mimo-v2.5-pro")
    agent = Agent(provider=provider, tools=registry)

    result = agent.run("Use the math.add tool to add 19.5 and 22.5, then answer briefly.")
    print(result)
