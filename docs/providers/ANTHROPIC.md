# Anthropic Provider

Anthropic Claude models with native tool-use API support. Uses Anthropic's block-based message format internally.

## Install

```bash
pip install "mtpx[anthropic]"
```

Or install the SDK directly:

```bash
pip install anthropic
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
   ANTHROPIC_API_KEY=sk-ant-your_key_here
   ```

3. Load it in your code **before** creating the provider:
   ```python
   from mtp import Agent

   Agent.load_dotenv_if_available()  # reads .env file
   ```

Get an API key at [console.anthropic.com](https://console.anthropic.com).

### Option 2: System environment variable

```bash
# Linux/macOS
export ANTHROPIC_API_KEY="sk-ant-..."

# Windows PowerShell
$env:ANTHROPIC_API_KEY="sk-ant-..."
```

## Quick Start

```python
from mtp import Agent
from mtp.providers import Anthropic

Agent.load_dotenv_if_available()  # loads ANTHROPIC_API_KEY from .env

provider = Anthropic(model="claude-sonnet-4-6")
tools = Agent.ToolRegistry()
agent = Agent(provider=provider, tools=tools)

reply = agent.run_loop("What is 25 * 4 + 10?")
print(reply)
```

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | `"claude-sonnet-4-6"` | Anthropic model ID |
| `api_key` | `str \| None` | `None` | API key (falls back to `ANTHROPIC_API_KEY` env var) |
| `max_tokens` | `int` | `1024` | Maximum tokens in the response |
| `temperature` | `float` | `0.0` | Sampling temperature |
| `tool_choice` | `str \| dict` | `"auto"` | `auto`, `any`/`required`, `none`, or a named-tool choice dictionary |
| `strict_tools` | `bool` | `False` | Add Anthropic's native `strict: true` to every tool definition |
| `output_config` | `dict \| None` | `None` | Native structured-output configuration using `format.type="json_schema"` |
| `thinking` | `dict \| None` | `None` | Anthropic thinking configuration, such as `{"type": "adaptive"}` |
| `client` | `Any \| None` | `None` | Pre-configured `anthropic.Anthropic` client instance |
| `async_client` | `Any \| None` | `None` | Pre-configured `anthropic.AsyncAnthropic` client instance |

## Capabilities

| Capability | Value |
|---|---|
| Tool calling | Yes |
| Parallel tool calls | Yes |
| Input modalities | text, image, file |
| Streaming | Native final-response streaming |
| Usage metrics | Rich |
| Structured output | Native JSON Schema (`output_config.format`) |
| Reasoning metadata | Yes (thinking blocks and thinking-token usage) |
| Native async | Yes |

## Recommended Models

- `claude-sonnet-4-6` — Balanced production model with a 1M-token context (default)
- `claude-haiku-4-5-20251001` — Fastest current production model
- `claude-opus-4-8` — More capable model for complex agentic work

## Strict Tools, Structured Outputs, and Thinking

```python
schema = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

provider = Anthropic(
    strict_tools=True,
    tool_choice="required",  # Anthropic `any`: at least one tool must be called
    output_config={"format": {"type": "json_schema", "schema": schema}},
    thinking={"type": "adaptive"},
)
```

You can force a named tool with
`tool_choice={"type": "tool", "name": "weather.get"}`. Strict mode guarantees
schema-conforming tool inputs for Anthropic's supported JSON Schema subset.

Structured output is native on supported current Claude models. MTP omits document
citations when `output_config` is set because Anthropic documents those features as
incompatible. When thinking is configured, MTP omits the incompatible sampling
temperature, reports thinking-token usage, and preserves signed thinking blocks
unchanged across tool-result turns.

## Multimodal Support

Anthropic supports images and files (PDFs, documents) natively:

```python
from mtp.media import Image, File

provider = Anthropic(model="claude-sonnet-4-6")
agent = Agent(provider=provider, tools=tools)

# With image
reply = agent.run_loop({
    "content": "Describe this image",
    "images": [Image(filepath="photo.jpg")],
})

# With PDF document
reply = agent.run_loop({
    "content": "Summarize this document",
    "files": [File(filepath="report.pdf")],
})
```

Local image data is accepted only as JPEG, PNG, GIF, or WebP and checked against
its declared media type. Binary documents must be valid PDFs; text documents must
be valid UTF-8. Remote sources must be absolute HTTP(S) URLs. Local and inline
attachment reads are bounded by MTP's shared media size limit.

## Full Example

```python
from mtp import Agent
from mtp.providers import Anthropic

Agent.load_dotenv_if_available()

provider = Anthropic(
    model="claude-sonnet-4-6",
    max_tokens=4096,
    temperature=0.0,
)

tools = Agent.ToolRegistry()
agent = Agent(provider=provider, tools=tools, debug_mode=True)

reply = agent.run_loop(
    "Calculate (25 * 4) + 10 and explain the steps.",
    max_rounds=4,
)
print(reply)
```

## Notes

- Anthropic uses a different internal message format (block-based with `tool_use` and `tool_result` content types), but MTP normalizes everything to the same event stream and `ExecutionPlan` semantics.
- System prompts are sent as the `system` parameter, not as a message role.
- See Anthropic's current documentation for [structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use), and [extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking).

## Source

`src/mtp/providers/anthropic_provider.py`
