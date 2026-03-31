import os
import pathlib
import sys

# Add src to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, ToolRegistry, load_dotenv_if_available
from mtp.providers import OpenRouter
from mtp.toolkits import CalculatorToolkit

def main():
    # 1. Load API Keys from .env
    load_dotenv_if_available()
    
    # 2. Setup Tools
    tools = ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())
    
    # 3. Setup OpenRouter Provider
    # Note: Using a free model for demonstration. 
    # Requires OPENROUTER_API_KEY in your .env file.
    provider = OpenRouter(
        model="qwen/qwen3.6-plus-preview:free",
        site_name="MTP Project Demo"
    )
    
    # 4. Create Agent
    agent = Agent(provider=provider, tools=tools, debug_mode=True)
    
    # 5. Run it!
    print("--- Starting OpenRouter Agent ---")
    response = agent.run("What is 1234 * 5678?")
    print(f"\nFinal Response: {response}")

if __name__ == "__main__":
    main()
