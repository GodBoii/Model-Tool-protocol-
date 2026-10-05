"""Run a text agent with the hosted adapters or OpenAI Responses.

Install the matching mtpx provider extra and set the documented key variable.
Run: python examples/hosted_compatible_agent.py --provider huggingface
"""

from __future__ import annotations

import argparse

from mtp import Agent
from mtp.providers import DashScope, DeepInfra, HuggingFace, OpenAIResponses


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("huggingface", "deepinfra", "dashscope", "openai_responses"),
        default="huggingface",
    )
    parser.add_argument("--model")
    parser.add_argument("--base-url", help="Use the endpoint matching your key's region.")
    args = parser.parse_args()
    Agent.load_dotenv_if_available()
    factories = {
        "huggingface": HuggingFace,
        "deepinfra": DeepInfra,
        "dashscope": DashScope,
        "openai_responses": OpenAIResponses,
    }
    options = {}
    if args.model:
        options["model"] = args.model
    if args.base_url:
        options["base_url"] = args.base_url
    provider = factories[args.provider](**options)
    agent = Agent(provider=provider, tools=Agent.ToolRegistry())
    try:
        print(agent.run_loop("Explain what a tool-calling agent does in two sentences."))
    finally:
        provider._client.close()


if __name__ == "__main__":
    main()
