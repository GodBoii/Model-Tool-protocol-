# Cohere Provider

Cohere provides Command models with native RAG grounding and multi-step agentic tool use built into the model.

## Install

```bash
pip install "mtpx[cohere]"
```

Or install the SDK directly:

```bash
pip install cohere
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
   COHERE_API_KEY=your_key_here
   ```

3. Load it in your code **before** creating the provider:
   ```python
   from mtp import Agent

   Agent.load_dotenv_if_available()  # reads .env file
   ```

Get a free trial key at [dashboard.cohere.com](https://dashboard.cohere.com) (no credit card required).

### Option 2: System environment variable

```bash
# Linux/macOS
export COHERE_API_KEY="..."

# Windows PowerShell
$env:COHERE_API_KEY="..."
```

## Quick Start

```python
from mtp import Agent
from mtp.providers import Cohere

Agent.load_dotenv_if_available()  # loads COHERE_API_KEY from .env

provider = Cohere(model="command-a-plus-05-2026")
tools = Agent.ToolRegistry()
agent = Agent(provider=provider, tools=tools)

reply = agent.run_loop("What is 25 * 4 + 10?")
print(reply)
```

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | `"command-a-plus-05-2026"` | Cohere model ID |
| `api_key` | `str \| None` | `None` | API key (falls back to `COHERE_API_KEY` env var) |
| `temperature` | `float` | `0.3` | Sampling temperature |
| `max_tokens` | `int` | `4096` | Maximum response tokens |
| `preamble` | `str \| None` | `None` | System prompt (Cohere's term for system instructions) |
| `force_single_step` | `bool` | `False` | Force single-step tool execution |
| `client` | `Any \| None` | `None` | Pre-configured `cohere.ClientV2` instance |

## Capabilities

| Capability | Value |
|---|---|
| Tool calling | Yes |
| Parallel tool calls | No |
| Input modalities | text |
| Streaming | Fallback |
| Usage metrics | Basic |
| Reasoning metadata | No |
| Native async | No (uses thread fallback) |

## Recommended Models

The SDK default is `command-a-plus-05-2026`. Verify availability, tools, and modalities in the provider catalog for your account. Explicit saved selections are preserved.

## Strengths

- **Native RAG / document grounding** — Cohere models are built for retrieval-augmented generation
- **Multi-step agentic tool use** — Built into the model, not just bolted on
- **Structured JSON output** — Reliable JSON generation
- **Complex reasoning chains** — Excellent at multi-hop reasoning

## Full Example

```python
from mtp import Agent
from mtp.providers import Cohere

Agent.load_dotenv_if_available()

provider = Cohere(
    model="command-a-plus-05-2026",
    temperature=0.3,
    max_tokens=4096,
    preamble="You are a precise tool-using assistant.",
)

tools = Agent.ToolRegistry()
agent = Agent(provider=provider, tools=tools, debug_mode=True)

reply = agent.run_loop(
    "Calculate (25 * 4) + 10 and explain.",
    max_rounds=3,
)
print(reply)
```

## Notes

- Uses Cohere V2 API (`ClientV2`) with OpenAI-compatible message format.
- Tool identifiers are mapped deterministically to valid native names and restored to their registered MTP names. Replay uses the same native identifiers as definitions.
- The `preamble` parameter is Cohere's equivalent of a system prompt. If a system message is already in the conversation, `preamble` is not injected.
- Text-only input (no image/audio/video/file support).

## Source

`src/mtp/providers/cohere_provider.py`

Restricted provider tool identifiers use a deterministic reversible mapping. Dotted MTP names remain unchanged in the registry and saved runtime calls.

These tables describe adapter behavior. Model-specific modality support and native streaming vary; SDK serialization tests do not establish live account access.
