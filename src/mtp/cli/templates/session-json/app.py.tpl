from mtp import Agent, JsonSessionStore
from mtp.providers import Groq
from mtp.toolkits import CalculatorToolkit


def main() -> None:
    Agent.load_dotenv_if_available()

    tools = Agent.ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())

    store = JsonSessionStore(db_path="tmp/mtp_json_db")
    provider = Groq(model="llama-3.3-70b-versatile")
    agent = Agent.MTPAgent(
        provider=provider,
        tools=tools,
        session_store=store,
        instructions="Use memory from session history when relevant.",
    )

    session_id = "demo-session"
    user_id = "demo-user"
    first = agent.run("Remember that my lucky number is 73.", session_id=session_id, user_id=user_id)
    second = agent.run("What is my lucky number?", session_id=session_id, user_id=user_id)
    print("First:", first)
    print("Second:", second)


if __name__ == "__main__":
    main()

