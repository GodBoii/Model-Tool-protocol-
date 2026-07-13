# Gemini Provider

Google Gemini models with native function calling and full multimodal support (text, image, audio, video, file).

## Install

```bash
pip install "mtpx[gemini]"
```

Or install the SDK directly:

```bash
pip install google-genai
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
   GEMINI_API_KEY=AIzaSyour_key_here
   ```

3. Load it in your code **before** creating the provider:
   ```python
   from mtp import Agent

   Agent.load_dotenv_if_available()  # reads .env file
   ```

Get a free API key at [aistudio.google.com](https://aistudio.google.com).

### Option 2: System environment variable

```bash
# Linux/macOS
export GEMINI_API_KEY="AIza..."

# Windows PowerShell
$env:GEMINI_API_KEY="AIza..."
```

## Quick Start

```python
from mtp import Agent
from mtp.providers import Gemini

Agent.load_dotenv_if_available()  # loads GEMINI_API_KEY from .env

provider = Gemini(model="gemini-3.5-flash")
tools = Agent.ToolRegistry()
agent = Agent(provider=provider, tools=tools)

reply = agent.run_loop("What is 25 * 4 + 10?")
print(reply)
```

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | `"gemini-3.5-flash"` | Gemini model ID |
| `api_key` | `str \| None` | `None` | API key (falls back to `GEMINI_API_KEY` env var) |
| `temperature` | `float` | `0.0` | Sampling temperature |
| `tool_choice` | `str \| dict` | `"auto"` | Function mode: `"auto"`, `"none"`, `"required"`/`"any"`, `"validated"`, a function name, or a function selector |
| `response_schema` | `Any \| None` | `None` | Native Google schema or Pydantic model for structured output |
| `response_json_schema` | `dict \| None` | `None` | Standard JSON Schema for native structured output |
| `response_mime_type` | `str \| None` | `None` | Response MIME type; defaults to `application/json` when a schema is set |
| `client` | `Any \| None` | `None` | Pre-configured `google.genai.Client` instance |
| `async_client` | `Any \| None` | `None` | Optional native async client (otherwise `client.aio` is used) |

## Capabilities

| Capability | Value |
|---|---|
| Tool calling | Yes |
| Parallel tool calls | Yes, correlated with Gemini call IDs |
| Input modalities | text, image, audio, video, file |
| Streaming | Native sync and async final streaming |
| Usage metrics | Rich |
| Reasoning metadata | Yes, when Gemini returns thought parts |
| Native async | Yes (`google.genai.Client.aio`) |
| Structured output | Native JSON Schema / Google schema |

## Recommended Models

- `gemini-3.5-flash` — Stable agentic and coding model with function calling (default)
- `gemini-3.1-flash-lite` — Stable lower-cost, high-throughput option
- `gemini-2.5-flash` — Stable 2.5-generation price-performance option

## Multimodal Support

Gemini supports all modalities natively:

```python
from mtp.media import Image, Audio, Video, File

# With image
reply = agent.run_loop({
    "content": "Describe this image",
    "images": [Image(filepath="photo.jpg")],
})

# With audio
reply = agent.run_loop({
    "content": "Transcribe this audio",
    "audios": [Audio(filepath="speech.mp3")],
})

# With video
reply = agent.run_loop({
    "content": "What happens in this video?",
    "videos": [Video(filepath="clip.mp4")],
})
```

## Function Calling Controls

MTP disables the Google SDK's automatic Python-function execution because MTP executes and
polices tools itself. `tool_choice` is translated to Gemini's `FunctionCallingConfig`:

```python
provider = Gemini(tool_choice="required")       # require a declared function
provider = Gemini(tool_choice="lookup_order")   # require this function
provider = Gemini(tool_choice="none")           # disable function calls
```

Gemini's native function-call IDs are preserved in session history and copied into each
function response. This correlates out-of-order parallel results, including multiple calls
to the same function name.

## Structured Output

Use standard JSON Schema with `response_json_schema`, or a Google/Pydantic schema with
`response_schema`:

```python
provider = Gemini(
    response_json_schema={
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
)
```

Do not set both schema parameters. MTP automatically requests `application/json` unless
`response_mime_type` is explicitly supplied.

## Full Example

```python
from mtp import Agent
from mtp.providers import Gemini

Agent.load_dotenv_if_available()

provider = Gemini(
    model="gemini-3.5-flash",
    temperature=0.0,
)

tools = Agent.ToolRegistry()
agent = Agent(provider=provider, tools=tools, debug_mode=True)

reply = agent.run_loop(
    "Calculate (25 * 4) + 10 and list files in the current directory.",
    max_rounds=4,
)
print(reply)
```

## Notes

- Gemini uses `function_declarations` in tools (not the OpenAI `function` wrapper format). MTP handles the translation automatically.
- Tool schemas are sanitized to remove unsupported JSON Schema keys before sending to Gemini.
- Thinking-model thought signatures and function-call IDs are round-tripped in their
  original parts so multi-turn function calling remains valid.
- Input modality support depends on the selected Gemini model.

## Source

`src/mtp/providers/gemini_provider.py`

Official references: [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/),
[Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling), and
[Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output).
