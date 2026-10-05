# Hugging Face Inference Providers

Install `pip install "mtpx[huggingface]"` and set `HF_TOKEN`. MTP does not load
`.env` implicitly in SDK code; call `Agent.load_dotenv_if_available()` if needed.

```python
from mtp import Agent
from mtp.providers import HuggingFace
from mtp.toolkits import CalculatorToolkit

Agent.load_dotenv_if_available()
tools = Agent.ToolRegistry()
tools.register_toolkit_loader("calculator", CalculatorToolkit())
provider = HuggingFace(
    model="openai/gpt-oss-120b:cerebras",
    max_tokens=512,
    parallel_tool_calls=True,
)
agent = Agent(provider=provider, tools=tools)
for event in agent.run_loop_events("Calculate 17+25 and 6*7.", max_rounds=3):
    if event["type"] == "text_chunk":
        print(event["chunk"], end="")
```

The endpoint defaults to `https://router.huggingface.co/v1`. A provider suffix
selects a route rather than MTP's provider class. The TUI preserves live named
routes from the catalog. Enter a custom ID for routing policies such as
`:fastest`; availability and supported parameters depend on the selected route.

Use `mtp tui --backend huggingface` or `/backend huggingface`. Setup accepts
the token, model, endpoint, and maximum output tokens. Keys stay out of chat
history. Model discovery sends only authenticated metadata GET requests.

The adapter advertises text only by default. Native text/tool streaming, async
thread bridging, usage, tool-name mapping, and reasoning-content replay share
the [compatible adapter](../COMPATIBLE_PROVIDERS.md). Setting parallel calls does
not make an unsupported model implement them. No live HF inference was used for
phase-two verification.

[Official endpoint and tool examples](https://huggingface.co/docs/inference-providers/en/guides/gpt-oss).
