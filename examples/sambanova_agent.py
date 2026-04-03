import os
import pathlib
import sys

# Add src to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent
from mtp.providers import SambaNova
from mtp.toolkits import CalculatorToolkit

def main():
    # 1. Load API Keys from .env
    Agent.load_dotenv_if_available()
    
    # 2. Setup Tools
    tools = Agent.ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())
    
    # 3. Setup SambaNova Provider
    # Requires SAMBANOVA_API_KEY in your .env file.
    # Base model hosted on SambaNova Cloud (ultra-fast).
    provider = SambaNova(
        model="Meta-Llama-3.3-70B-Instruct",
        temperature=0.0
    )
    
    # 4. Create Agent
    agent = Agent(provider=provider, tools=tools, debug_mode=True)
    
    # 5. Run it!
    print("--- Starting SambaNova Agent ---")
    response = agent.run("What is 9876 * 5432?")
    print(f"\nFinal Response: {response}")

if __name__ == "__main__":
    main()
