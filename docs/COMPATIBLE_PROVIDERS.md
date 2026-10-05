# Compatible provider configuration

`OpenAICompatible` is an SDK-only adapter for a custom Chat Completions endpoint.
The hosted `HuggingFace`, `DeepInfra`, and `DashScope` providers share its
implementation; old adapters keep their existing behavior.

```python
from mtp.providers import OpenAICompatible

provider = OpenAICompatible(
    provider_name="my-service",
    model="my-model",
    base_url="https://inference.example/v1",
    api_key="your-service-key",
    max_tokens=512,
    parallel_tool_calls=True,
)
```

| Option | Default | Behavior |
|---|---|---|
| `model` | Required | Exact model/route ID, never silently changed |
| `base_url` | Required for generic adapter | HTTPS for remote endpoints; HTTP permitted only for loopback |
| `api_key` | Required unless a client is injected | Credential belongs to this client, never a global SDK variable |
| `provider_name` | `compatible` | Label in events and capabilities |
| `temperature` | `None` | Omitted unless explicitly supplied |
| `max_tokens` | `1024` | Positive integer, or `None` to omit |
| `parallel_tool_calls` | `None` | Omitted unless configured; DashScope defaults to true |
| `tool_choice` | `auto` | Native choice; forced tool names use the same reversible mapping |
| `timeout_seconds` | `60` | Finite positive client timeout |
| `extra_body` | Empty | Provider-specific options; cannot override protected protocol/credential fields |
| `input_modalities` | `text` | Tuple containing text and optionally image; selected model must support it |
| `stream_include_usage` | `True` | Requests final usage chunks; false omits the stream option |
| `client` | `None` | Inject a configured OpenAI client for tests or service-specific settings |

Native streaming assembles fragments by tool-call index, including interleaved
calls and split names/arguments. Only complete JSON objects with unique nonempty
IDs become executable plans. Truncated/filtered streams and missing terminal
markers fail. Streams close on completion or error. Async APIs bridge blocking
SDK iterators to workers; they do not advertise native async SDK support.

The selected provider's errors propagate without broad compatibility retries.
Text-only capabilities are conservative defaults, not universal model claims.
Requesting a parallel option does not guarantee model support. Explicitly
configure provider-specific reasoning or format options only when its official
model documentation permits them.

New TUI endpoint/budget settings are saved separately from chat history. These
settings do not publish keys or create provider accounts. Generic endpoint
configuration is available through the SDK; the TUI exposes the named providers.
