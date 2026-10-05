# OpenRouter Provider

OpenRouter provides a unified API to access models from multiple providers. MTP uses the OpenAI SDK with `https://openrouter.ai/api/v1` and an OpenRouter API key. No separate `openrouter` Python package is required.

## Install

```bash
pip install "mtpx[openrouter]"
```

This installs the `openai` SDK (used for the OpenAI-compatible API):

```bash
pip install openai
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
   OPENROUTER_API_KEY=sk-or-your_key_here
   ```

3. Load it in your code **before** creating the provider:
   ```python
   from mtp import Agent

   Agent.load_dotenv_if_available()  # reads .env file
   ```

Get an API key at [openrouter.ai](https://openrouter.ai).

The dotenv loader does not overwrite an existing environment variable. If you replace a key in `.env`, an older `OPENROUTER_API_KEY` inherited by the process can still take priority.

### Option 2: System environment variable

```bash
# Linux/macOS
export OPENROUTER_API_KEY="sk-or-..."

# Windows PowerShell
$env:OPENROUTER_API_KEY="sk-or-..."
```

### TUI saved keys

Use `/apikey openrouter` to save or replace the key. The TUI uses its saved key first, then `OPENROUTER_API_KEY`. Updating `.env` or the environment does not replace a saved key. Removing the saved key allows the TUI to use the environment key.

By default, saved provider settings live in `~/.mtp/sessions/tui_provider_settings.json`, beside the session store. A custom `--session-db` changes this location. Saving a key checks its format, not its validity with OpenRouter. Loading the public model catalog also does not prove that the key can run inference.

## Quick Start

```python
from mtp import Agent
from mtp.providers import OpenRouter

Agent.load_dotenv_if_available()  # loads OPENROUTER_API_KEY from .env

provider = OpenRouter(model="qwen/qwen-2.5-72b-instruct")
tools = Agent.ToolRegistry()
agent = Agent(provider=provider, tools=tools)

reply = agent.run_loop("What is 25 * 4 + 10?")
print(reply)
```

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | `"qwen/qwen-2.5-72b-instruct"` | OpenRouter model ID (format: `provider/model`) |
| `api_key` | `str \| None` | `None` | API key (falls back to `OPENROUTER_API_KEY` env var) |
| `site_url` | `str \| None` | `None` | Your site URL (sent as `HTTP-Referer` header for rankings) |
| `site_name` | `str \| None` | `None` | Your app name (sent as `X-Title` header) |
| `temperature` | `float` | `0.0` | Sampling temperature |
| `tool_choice` | `str \| dict` | `"auto"` | Tool selection strategy |
| `max_tokens` | `int \| None` | `None` | Positive output-token limit for planning and final answers. `None` leaves the limit to OpenRouter. Reasoning can consume this budget too. |
| `client` | `Any \| None` | `None` | Pre-configured `openai.OpenAI` client instance |

## Capabilities

| Capability | Value |
|---|---|
| Tool calling | Yes (model-dependent) |
| Parallel tool calls | No |
| Input modalities | text, image, audio, video, file |
| Streaming | Fallback |
| Usage metrics | Rich |
| Reasoning metadata | No |
| Native async | No (uses thread fallback) |

## Choosing a model and budget

The default `qwen/qwen-2.5-72b-instruct` is a paid model. The adapter sends the exact selected ID, including any `:free` suffix. It does not append `:free`, choose another model, or send a fallback-model list.

Check the [current catalog](https://openrouter.ai/models) or `GET https://openrouter.ai/api/v1/models` for the exact model ID, pricing, and `supported_parameters`. Free variants commonly end in `:free`, but some zero-priced models have no suffix. A name or suffix alone does not establish current pricing. For a tool agent, select a model supporting `tools` and `tool_choice`.

An API key's credit limit is a spending cap, not a deposit into the account. Paid requests need an account balance as well as room under the key's cap. For a small paid budget, set an output-token limit:

```python
provider = OpenRouter(model="qwen/qwen-2.5-72b-instruct", max_tokens=1024)
```

Each agent round can make another model request. The token limit applies to each request, not the entire run, and does not guarantee that a paid request fits the remaining balance. In the TUI, an optional `max_tokens` field in the saved `providers.openrouter` entry uses the same limit. Restart the TUI after editing that file.

Free requests still need valid credentials and are subject to account and provider limits. A negative account balance can block even free models. Free request quotas are separate from dollar spending limits. See [OpenRouter's current limits](https://openrouter.ai/docs/api/reference/limits).

Zero token pricing does not prove that OpenRouter will admit a particular account to that model. In a live check on October 5, 2026, `inclusionai/ling-3.1-flash` had zero prompt and completion prices in the catalog but returned `402` with "This account never purchased credits" for a free-tier key. `apodex/apodex-1.1-mini:free` succeeded with that same key. MTP sent the exact IDs in both requests. Check the actual response and account access rather than treating every zero-priced ID as interchangeable with a `:free` variant.

## Diagnosing errors

| Response | Meaning | Next step |
|---|---|---|
| `401`, including `User not found` | OpenRouter rejected the credentials | Replace the complete key with `/apikey openrouter`. Check whether a saved key overrides the environment. Switching to a free model does not fix authentication. |
| `402` with `limit_source: openrouter_credits` | OpenRouter rejected the request against account credits or its estimated paid-request budget | Check the account, exact model pricing, and balance. For paid requests, reduce `max_tokens` or add credits. A free model returning this error needs account/key investigation; do not assume the model is paid. |
| `402` with `limit_source: openrouter_key_limit` | The key's spending cap is exhausted | Check the cap, remaining allowance, and reset time. |
| `402` with `limit_source: openrouter_in_flight_budget` | Running or recently completed paid requests fill the in-flight budget | Wait for the response's `Retry-After` interval. |
| `429` | OpenRouter's free quota or a provider rate limit was reached | Check the quota and response headers; wait before retrying. |

Check the **same key the TUI resolves** using the read-only `GET /api/v1/key` endpoint. This PowerShell example checks the environment key only, which may differ from the TUI's saved key:

```powershell
$headers = @{ Authorization = "Bearer $env:OPENROUTER_API_KEY" }
$keyInfo = Invoke-RestMethod -Uri "https://openrouter.ai/api/v1/key" -Headers $headers
$keyInfo.data | Select-Object limit, limit_remaining, is_free_tier, free_model_daily_requests
```

Never paste the key into chat or commit it. A successful key check establishes authentication, not model availability. See [OpenRouter's error reference](https://openrouter.ai/docs/api/reference/errors-and-debugging).

## Full Example

```python
from mtp import Agent
from mtp.providers import OpenRouter

Agent.load_dotenv_if_available()

provider = OpenRouter(
    model="qwen/qwen-2.5-72b-instruct",
    site_url="https://myapp.com",
    site_name="My Agent App",
    temperature=0.0,
    max_tokens=1024,
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

- OpenRouter uses the OpenAI-compatible API at `https://openrouter.ai/api/v1`.
- Model IDs use the format `provider/model-name` (e.g., `anthropic/claude-3.5-sonnet`).
- Tool calling support and quality depends on the underlying model.
- Free variants commonly use a `:free` suffix; check current pricing for the exact ID.
- The `site_url` and `site_name` parameters help with OpenRouter rankings and attribution.

## Source

`src/mtp/providers/openrouter_provider.py`

These tables describe adapter behavior. Model-specific modality support and native streaming vary; SDK serialization tests do not establish live account access.
