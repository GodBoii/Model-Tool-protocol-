from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, ToolRegistry, ToolRiskLevel, ToolSpec, load_dotenv_if_available
from mtp.providers import GroqToolCallingProvider


def list_repos(username: str) -> dict:
    # Replace with real GitHub API logic in your project.
    return {"username": username, "repos": ["mtp-core", "mtp-docs", "mtp-examples"]}


def main() -> None:
    # Provider-agnostic env loading for all future adapters (Groq/OpenAI/Claude/Gemini/etc.).
    load_dotenv_if_available()

    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec(
            name="github.list_repos",
            description="List public repositories for a given GitHub username.",
            input_schema={
                "type": "object",
                "properties": {"username": {"type": "string"}},
                "required": ["username"],
                "additionalProperties": False,
            },
            risk_level=ToolRiskLevel.READ_ONLY,
            cache_ttl_seconds=60,
        ),
        list_repos,
    )

    provider = GroqToolCallingProvider(
        model="llama-3.3-70b-versatile",
        system_prompt=(
            "You are an agent that uses tools when needed. "
            "Call github.list_repos if user asks about repositories."
        ),
    )
    agent = Agent(provider=provider, registry=registry)
    reply = agent.run("write a poem on github")
    print(reply)


if __name__ == "__main__":
    main()
