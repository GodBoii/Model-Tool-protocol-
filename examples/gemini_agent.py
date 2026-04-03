import os
import pathlib
import sys

# Add src to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent
from mtp.providers import Gemini
from mtp.toolkits import CalculatorToolkit

def main():
    # 1. Load API Keys from .env
    Agent.load_dotenv_if_available()
    
    # 2. Setup Tools
    tools = Agent.ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())
    
    # 3. Setup Gemini Provider
    provider = Gemini(
        model="gemini-2.0-flash",
    )
    
    # 4. Create Agent
    agent = Agent(provider=provider, tools=tools, debug_mode=True)
    
    # 5. Run it!
    print("--- Starting Gemini Agent ---")
    response = agent.run("What is 1234 * 5678?")
    print(f"\nFinal Response: {response}")

if __name__ == "__main__":
    main()
