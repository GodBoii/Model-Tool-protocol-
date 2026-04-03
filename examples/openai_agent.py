import pathlib
import sys
import os

# Add src to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent
from mtp.providers import OpenAI
from mtp.toolkits import CalculatorToolkit

def main():
    Agent.load_dotenv_if_available()
    tools = Agent.ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())
    
    provider = OpenAI(model="gpt-4o-mini", temperature=0.0)
    agent = Agent(provider=provider, tools=tools, debug_mode=True)
    
    print("--- Starting OpenAI Agent ---")
    response = agent.run("What is 100 + 100?")
    print(f"\nFinal Response: {response}")

if __name__ == "__main__":
    main()
