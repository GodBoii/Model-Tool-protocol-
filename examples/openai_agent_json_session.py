import pathlib
import sys

# Add src to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, JsonSessionStore
from mtp.providers import OpenAI
from mtp.toolkits import CalculatorToolkit


def main() -> None:
    Agent.load_dotenv_if_available()

    tools = Agent.ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())

    provider = OpenAI(model="gpt-4o")
    session_store = JsonSessionStore(db_path="tmp/mtp_json_db")
    agent = Agent.MTPAgent(provider=provider, tools=tools, session_store=session_store, debug_mode=True)

    session_id = "demo_openai_json_session"

    first = agent.run("My favorite number is 73.", session_id=session_id, user_id="demo-user")
    print(f"First response: {first}")

    second = agent.run("What is my favorite number?", session_id=session_id, user_id="demo-user")
    print(f"Second response: {second}")


if __name__ == "__main__":
    main()
