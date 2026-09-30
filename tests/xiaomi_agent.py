from mtp import Agent 
from mtp.providers import Xiaomi
from mtp.toolkits import CalculatorToolkit

tools = Agent.ToolRegistry()
tools.register_toolkit_loader("calculator", CalculatorToolkit())

agent = Agent.MTPAgent(
    provider=Xiaomi(model="mimo-v2.5-pro"),
    tools=tools,
    instructions="Act as an helpful assistant",
    debug_mode=True,
)

agent.print_response(
    "solve this math problem -> 15 + 4 * (18 - 6) / 3",
    stream=True,
)