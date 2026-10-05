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

provider = Gemini(model="gemini-3.8-flash")
tools = Agent.ToolRegistry()
agent = Agent(provider=provider, tools=tools)

reply = agent.run_loop("What is 25 * 4 + 10?")
print(reply)
```

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | `"gemini-3.8-flash"` | Gemini model ID |
| `api_key` | `str \| None` | `None` | API key (falls back to `GEMINI_API_KEY` env var) |
| `temperature` | `float` | `0.0` | Sampling temperature |
| `client` | `Any \| None` | `None` | Pre-configured `google.genai.Client` instance |

## Capabilities

| Capability | Value |
|---|---|
| Tool calling | Yes |
| Parallel tool calls | Yes |
| Input modalities | text, image, audio, video, file |
| Streaming | Fallback |
| Usage metrics | Rich |
| Reasoning metadata | No |
| Native async | No (uses thread fallback) |

## Recommended Models

The SDK default is `gemini-3.8-flash`. Verify availability, tools, and modalities in the provider catalog for your account. Explicit saved selections are preserved.

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

## Full Example

```python
from mtp import Agent
from mtp.providers import Gemini

Agent.load_dotenv_if_available()

provider = Gemini(
    model="gemini-3.8-flash",
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
- Gemini supports parallel native function calls. MTP preserves signed model parts when replaying tool rounds.

## Source

`src/mtp/providers/gemini_provider.py`

These tables describe adapter behavior. Model-specific modality support and native streaming vary; SDK serialization tests do not establish live account access.
