from mtp import Agent
from mtp.providers import Groq
from mtp.toolkits import CalculatorToolkit


def main() -> None:
    Agent.load_dotenv_if_available()

    tools = Agent.ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())

    provider = Groq(model="llama-3.3-70b-versatile")
    agent = Agent.MTPAgent(
        provider=provider,
        tools=tools,
        instructions="Use tools when useful and keep answers concise.",
    )

    result = agent.run("What is (25 * 4) + 10?")
    print(result)


if __name__ == "__main__":
    main()

