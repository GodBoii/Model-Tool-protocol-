# OpenAI provider

MTP's OpenAI adapter uses the Chat Completions API with native function calling,
structured outputs, sync and async clients, and streaming for both planning and
final answers.

## Install

```bash
pip install "mtpx[openai]"
```

Set `OPENAI_API_KEY` in the environment or pass `api_key=` explicitly. A `.env`
file can be loaded before constructing the provider:

```python
from mtp import Agent

Agent.load_dotenv_if_available()
```

Create API keys in the [OpenAI dashboard](https://platform.openai.com/api-keys).

## Quick start

```python
from mtp import Agent
from mtp.providers import OpenAI

Agent.load_dotenv_if_available()

provider = OpenAI(model="gpt-4o")
agent = Agent(provider=provider, tools=Agent.ToolRegistry())
print(agent.run_loop("What is 25 * 4 + 10?"))
```

`gpt-4o` remains MTP's compatibility default. Select a current model that
supports Chat Completions and function calling for new deployments; model
features vary, so verify the selected model in OpenAI's
[model catalog](https://developers.openai.com/api/docs/models).

## Parameters

| Parameter | Type | Default | Description |
|---|---|---:|---|
| `model` | `str` | `"gpt-4o"` | OpenAI model ID |
| `api_key` | `str \| None` | `None` | Falls back to `OPENAI_API_KEY` |
| `temperature` | `float \| None` | `0.0` | Sampling temperature; use `None` to omit it for models that reject sampling controls |
| `tool_choice` | `str \| dict` | `"auto"` | `"auto"`, `"none"`, `"required"`, a named function selector, or an allowed-tools selector |
| `parallel_tool_calls` | `bool` | `True` | Allow more than one function call in a model turn |
| `strict_tools` | `bool` | `False` | Add `strict: true` to each function definition |
| `response_format` | `dict \| None` | `None` | Native `text`, `json_object`, or `json_schema` Chat Completions format |
| `reasoning_effort` | `str \| None` | `None` | Forward the documented reasoning-effort level supported by the selected model |
| `max_completion_tokens` | `int \| None` | `None` | Limit visible output plus reasoning tokens |
| `stream_include_usage` | `bool` | `True` | Request the terminal stream usage chunk |
| `stream_include_obfuscation` | `bool \| None` | `None` | Explicitly enable/disable stream payload obfuscation; `None` uses the API default |
| `timeout` | `float \| None` | `None` | Per-request SDK timeout in seconds |
| `client` | `Any \| None` | `None` | Preconfigured `openai.OpenAI` client |
| `async_client` | `Any \| None` | `None` | Preconfigured `openai.AsyncOpenAI` client |

The same options are applied to synchronous, asynchronous, planning, final,
and streaming requests. `tool_choice` and `parallel_tool_calls` are sent only
when the request has function tools. `stream_options` is sent only for streamed
requests and is omitted when neither stream option is configured.

## Strict functions and structured output

```python
provider = OpenAI(
    model="gpt-4o",
    strict_tools=True,
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        },
    },
)
```

OpenAI strict function schemas require every property to be listed in
`required` and object schemas to set `additionalProperties: false`. MTP forwards
the supplied schema without silently changing its meaning. See OpenAI's
[function calling](https://developers.openai.com/api/docs/guides/function-calling)
and [structured output](https://developers.openai.com/api/docs/guides/structured-outputs)
guides.

## Reasoning and usage

`reasoning_effort` is forwarded to Chat Completions. Supported values are
model-specific. OpenAI does not expose raw reasoning tokens through the API, so
the adapter does not synthesize reasoning text or emit `reasoning_chunk`
events. When OpenAI returns `completion_tokens_details.reasoning_tokens`, MTP
preserves the count as `usage.reasoning_tokens` in action metadata and stream
usage. Reasoning summaries are a separate Responses API feature and are not
provided by this Chat Completions adapter. See the official
[reasoning guide](https://developers.openai.com/api/docs/guides/reasoning).

Streaming requests ask for the terminal usage chunk by default. If a stream is
interrupted before that chunk arrives, usage may be unavailable, matching the
API contract.

## Capabilities

| Capability | Value |
|---|---|
| Function calling | Native |
| Parallel function calls | Configurable |
| Structured output | Native JSON object / JSON schema when configured |
| Planning stream | Native |
| Final-answer stream | Native |
| Usage metrics | Rich, including reasoning-token counts when returned |
| Raw reasoning metadata | No |
| Async | Native `AsyncOpenAI` |

Text, image, audio, and file input conversion is available in the adapter, but
actual modality support is model-specific. Do not infer model support solely
from the provider-level capability envelope.

## Rate-limit tracking

For non-streamed calls, the provider uses the SDK's raw-response interface when
available and records `x-ratelimit-*` and `retry-after` headers. Planning action
metadata includes those headers; final-call headers are retained on
`_last_finalize_rate_limits` for diagnostics.

## Source

`src/mtp/providers/openai_provider.py`
