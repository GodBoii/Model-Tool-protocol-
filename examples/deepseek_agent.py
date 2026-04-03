import pathlib
import sys
import os

# Add src to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent
from mtp.providers import DeepSeek
from mtp.toolkits import CalculatorToolkit

def main():
    Agent.load_dotenv_if_available()
    tools = Agent.ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())
    
    # DeepSeek-V3 is excellent at logic and tool usage.
    provider = DeepSeek(
        model="deepseek-chat", # This is V3
        temperature=0.0
    )
    
    agent = Agent(provider=provider, tools=tools, debug_mode=True)
    
    print("--- Starting DeepSeek Agent ---")
    response = agent.run("What is 1234 + 5678?")
    print(f"\nFinal Response: {response}")

if __name__ == "__main__":
    main()
