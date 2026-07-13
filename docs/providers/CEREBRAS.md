# Cerebras Provider

Cerebras runs Llama models on wafer-scale chips, delivering the fastest inference available (~2000 tokens/sec).

## Install

```bash
pip install "mtpx[cerebras]"
```

This installs the native Cerebras SDK. To install it directly:

```bash
pip install cerebras-cloud-sdk
```

## API Key Setup

### Option 1: `.env` file (recommended)

1. Install dotenv support:
   ```bash
   pip install python-dotenv
   ```
   Or with the MTP extra:
   ```bash
   pip install "mtpx[dotenv]"
   ```

2. Create a `.env` file in your project root:
   ```
   CEREBRAS_API_KEY=csk-your_key_here
   ```

3. Load it in your code **before** creating the provider:
   ```python
   from mtp import Agent

   Agent.load_dotenv_if_available()  # reads .env file
   ```

Get a free API key at [cloud.cerebras.ai](https://cloud.cerebras.ai) (no credit card required).

### Option 2: System environment variable

```bash
# Linux/macOS
export CEREBRAS_API_KEY="csk-..."

# Windows PowerShell
$env:CEREBRAS_API_KEY="csk-..."
```

## Quick Start

```python
from mtp import Agent
from mtp.providers import Cerebras

Agent.load_dotenv_if_available()  # loads CEREBRAS_API_KEY from .env

provider = Cerebras(model="llama-4-scout-17b-16e-instruct")
tools = Agent.ToolRegistry()
agent = Agent(provider=provider, tools=tools)

reply = agent.run_loop("What is 25 * 4 + 10?")
print(reply)
```

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | `"llama-4-scout-17b-16e-instruct"` | Cerebras model ID |
| `api_key` | `str \| None` | `None` | API key (falls back to `CEREBRAS_API_KEY` env var) |
| `temperature` | `float` | `0.0` | Sampling temperature |
| `tool_choice` | `str \| dict` | `"auto"` | Tool selection strategy |
| `parallel_tool_calls` | `bool` | `True` | Allow parallel tool calls |
| `response_format` | `dict \| None` | `None` | Native `json_object` or strict `json_schema` response format |
| `client` | `Any \| None` | `None` | Pre-configured `Cerebras` client instance |
| `async_client` | `Any \| None` | `None` | Pre-configured `AsyncCerebras` client instance |

## Capabilities

| Capability | Value |
|---|---|
| Tool calling | Yes |
| Parallel tool calls | Yes (configurable) |
| Input modalities | text |
| Streaming | Native, except legacy `json_object` mode |
| Usage metrics | Rich |
| Reasoning metadata | No |
| Structured output | Native JSON object/schema when configured |
| Native async | Yes with `AsyncCerebras`; safe thread bridge for injected sync-only clients |

## Recommended Models

- `llama-4-scout-17b-16e-instruct` — Best tool calling (default)
- `llama-3.3-70b` — Strong general purpose
- `llama3.1-8b` — Fastest

## Full Example

```python
from mtp import Agent
from mtp.providers import Cerebras

Agent.load_dotenv_if_available()

provider = Cerebras(
    model="llama-4-scout-17b-16e-instruct",
    temperature=0.0,
    parallel_tool_calls=True,
)

tools = Agent.ToolRegistry()
agent = Agent(provider=provider, tools=tools, debug_mode=True)

reply = agent.run_loop(
    "Calculate (25 * 4) + 10",
    max_rounds=3,
)
print(reply)
```

## Notes

- Cerebras uses the native `cerebras-cloud-sdk`.
- Text-only input (no image/audio/video support).
- The `parallel_tool_calls` parameter is gracefully handled if the SDK version doesn't support it.
- Cerebras documents legacy `json_object` as incompatible with streaming. MTP automatically uses one non-streaming completion for that mode.
- Strict JSON schemas must follow Cerebras schema requirements, including an object root and `additionalProperties: false` for object definitions.

## Source

`src/mtp/providers/cerebras_provider.py`

Official references: [structured outputs](https://inference-docs.cerebras.ai/capabilities/structured-outputs), [Chat Completions API](https://inference-docs.cerebras.ai/api-reference/chat-completions).
