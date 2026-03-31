import os
import pathlib
import sys

# Add src to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, ToolRegistry, load_dotenv_if_available
from mtp.providers import Gemini
from mtp.toolkits import CalculatorToolkit

def main():
    # 1. Load API Keys from .env
    load_dotenv_if_available()
    
    # 2. Setup Tools
    registry = ToolRegistry()
    registry.register_toolkit_loader("calculator", CalculatorToolkit())
    
    # 3. Setup Gemini Provider
    # Note: Using a free model for demonstration. 
    # Requires GEMINI_API_KEY in your .env file.
    provider = Gemini(
        model="gemini-3-flash-preview",
    )
    
    # 4. Create Agent
    agent = Agent(provider=provider, registry=registry, debug_mode=True)
    
    # 5. Run it!
    print("--- Starting Gemini Agent ---")
    response = agent.run("What is 1234 * 5678?, after that write a poem on donald trump")
    print(f"\nFinal Response: {response}")

if __name__ == "__main__":
    main()
