import pathlib
import sys
import os

# Add src to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent
from mtp.providers import TogetherAI
from mtp.toolkits import CalculatorToolkit

def main():
    Agent.load_dotenv_if_available()
    tools = Agent.ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())
    
    # Together AI hosts 200+ open-source models.
    provider = TogetherAI(
        model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        temperature=0.0
    )
    
    agent = Agent(provider=provider, tools=tools, debug_mode=True)
    
    print("--- Starting Together AI Agent ---")
    response = agent.run("What is 1024 * 1024?")
    print(f"\nFinal Response: {response}")

if __name__ == "__main__":
    main()
