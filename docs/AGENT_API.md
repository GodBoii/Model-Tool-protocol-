# Agent API Reference (Detailed)

This guide explains every major parameter and runtime option for creating and running agents in MTP.

It is written in the same spirit as Agno's agent docs, but mapped to MTP's architecture:
- Agno often exposes `model=` directly on `Agent`.
- MTP keeps model/provider details inside a `provider` object and passes that provider into `Agent`/`MTPAgent`.

## 1) Mental Model

In MTP, there are two layers:
1. Provider layer: model + API details (for example OpenAI/Groq/Anthropic model names, API keys, tool-call parsing).
2. Agent layer: orchestration loop (rounds, tool execution, event streaming, strict dependencies, debug logging).

So when you ask "what model parameter can I set on Agent?", the answer is:
- `model` is set on the provider instance.
- `provider` is passed to `Agent` or `MTPAgent`.

## 2) `Agent` Constructor Parameters

Source: [agent.py](/c:/Users/prajw/Downloads/MTP/src/mtp/agent.py)

```python
Agent(
    provider: ProviderAdapter,
    registry: ToolRegistry,
    *,
    debug_mode: bool = False,
    debug_logger: Callable[[str], None] | None = None,
    debug_max_chars: int = 600,
    strict_dependency_mode: bool = False,
    instructions: str | None = None,
    system_instructions: str | None = None,
    stream_chunk_size: int = 40,
    max_history_messages: int = 200,
)
```

### `provider` (required)
- Type: `ProviderAdapter`
- Purpose: Connects the agent to a model backend and translates model responses into `AgentAction`.
- Important: model name/config is controlled here (for example `OpenAIToolCallingProvider(model="gpt-4o")`).

### `registry` (required)
- Type: `ToolRegistry`
- Purpose: Holds tools/toolkits and executes tool plans returned by the provider.

### `debug_mode`
- Type: `bool`
- Default: `False`
- Purpose: Enables verbose runtime logs (`[MTP DEBUG] ...`) for planning/execution visibility.

### `debug_logger`
- Type: `Callable[[str], None] | None`
- Default: `print`
- Purpose: Custom log sink for debug messages.

### `debug_max_chars`
- Type: `int`
- Default: `600`
- Purpose: Truncation limit for debug payload logging.

### `strict_dependency_mode`
- Type: `bool`
- Default: `False`
- Purpose: Enforces explicit dependency wiring for multi-call same-toolkit batches.
- Behavior: if violations are detected, agent injects a replan instruction and continues next round.

### `instructions`
- Type: `str | None`
- Default: `None`
- Purpose: Your app/task-specific system instruction.
- Added as a system message at run start (after built-in MTP system instructions).

### `system_instructions`
- Type: `str | None`
- Default: internal MTP instructions (`DEFAULT_MTP_SYSTEM_INSTRUCTIONS`)
- Purpose: Override/replace the default core orchestration system instructions.

### `stream_chunk_size`
- Type: `int`
- Default: `40`
- Purpose: Chunk size when non-stream final text is chunked for streaming interfaces.
- If `<= 0`, full text is yielded in one chunk.

### `max_history_messages`
- Type: `int`
- Default: `200`
- Purpose: Bounds retained conversation history in memory.
- System messages are retained; non-system messages are trimmed from oldest first.

## 3) `MTPAgent` Constructor Parameters

Source: [simple_agent.py](/c:/Users/prajw/Downloads/MTP/src/mtp/simple_agent.py)

`MTPAgent` is a convenience wrapper around `Agent` with nearly the same config:

```python
MTPAgent(
    *,
    provider: ProviderAdapter,
    registry: ToolRegistry,
    debug_mode: bool = False,
    strict_dependency_mode: bool = False,
    instructions: str | None = None,
    system_instructions: str | None = None,
    stream_chunk_size: int = 40,
    max_history_messages: int = 200,
)
```

Use `MTPAgent` when you want simpler high-level methods (`run`, `print_response`) without directly managing lower-level orchestration calls.

## 4) Runtime Methods and Their Parameters

## `Agent` methods

### `run(user_text: str) -> str`
- Single-round helper (`run_loop(..., max_rounds=1)`).

### `run_loop(user_text: str, max_rounds: int = 5) -> str`
- Multi-round tool planning/execution loop.
- `max_rounds` must be `>= 1`.

### `arun(user_text: str) -> str`, `arun_loop(..., max_rounds=5)`
- Async equivalents.
- Use these in existing asyncio applications.

### `run_loop_stream(user_text: str, max_rounds: int = 5) -> Iterator[str]`
- Streams text chunks.

### `run_loop_events(user_text: str, max_rounds: int = 5, stream_final: bool = True)`
- Emits structured events (`run_started`, `plan_received`, `tool_started`, `tool_finished`, `text_chunk`, `run_completed`).

### `arun_loop_events(...)`
- Async event-stream variant.

## `MTPAgent` methods

### `run(prompt: str, max_rounds: int = 5) -> str`
### `run_stream(prompt: str, max_rounds: int = 5) -> Iterator[str]`
### `arun(prompt: str, max_rounds: int = 5) -> str`
### `run_events(prompt: str, max_rounds: int = 5, stream_final: bool = True)`
### `arun_events(prompt: str, max_rounds: int = 5, stream_final: bool = True)`

### `print_response(prompt, max_rounds=5, stream=False, stream_events=False)`
- `stream=False`, `stream_events=False`: prints one final response.
- `stream=True`, `stream_events=False`: prints text chunks.
- `stream_events=True`: prints JSON event stream (`stream` controls whether final text is chunked in events).

`stream_events` is equivalent in spirit to Agno's runtime event streaming toggles.

## 5) Where `model` Is Configured (Important)

In MTP, `model` is set on provider constructors, not on `Agent`.

Examples:

```python
from mtp.providers import OpenAIToolCallingProvider, OpenRouterToolCallingProvider
from mtp.providers import AnthropicToolCallingProvider, GeminiToolCallingProvider

openai_provider = OpenAIToolCallingProvider(model="gpt-4o")
openrouter_provider = OpenRouterToolCallingProvider(model="qwen/qwen3.6-plus-preview:free")
anthropic_provider = AnthropicToolCallingProvider(model="claude-3-5-sonnet-20241022")
gemini_provider = GeminiToolCallingProvider(model="gemini-2.0-flash")
```

Then pass provider into agent:

```python
agent = MTPAgent(provider=openai_provider, registry=registry, debug_mode=True)
```

## 6) Common Creation Patterns

## Minimal

```python
from mtp import MTPAgent, ToolRegistry
from mtp.providers import OpenAIToolCallingProvider
from mtp.toolkits import register_local_toolkits

registry = ToolRegistry()
register_local_toolkits(registry, base_dir=".")

provider = OpenAIToolCallingProvider(model="gpt-4o")
agent = MTPAgent(provider=provider, registry=registry)
print(agent.run("List files in this folder.", max_rounds=3))
```

## Debug + strict dependencies + events

```python
agent = MTPAgent(
    provider=provider,
    registry=registry,
    debug_mode=True,
    strict_dependency_mode=True,
    instructions="Prefer precise tool usage and concise summaries.",
    max_history_messages=300,
)

agent.print_response(
    "Calculate 23*19 and show relevant files.",
    max_rounds=4,
    stream=True,
    stream_events=True,
)
```

## 7) Agno-to-MTP Parameter Mapping

- Agno `model=` on `Agent` -> MTP provider instance with `model=...`
- Agno `instructions` -> MTP `instructions`
- Agno `debug_mode` -> MTP `debug_mode`
- Agno `stream`/`stream_events` runtime flags -> MTP `print_response(stream=..., stream_events=...)`
- Agno tool configuration -> MTP `ToolRegistry` + toolkit loaders

## 8) Pitfalls and Tips

1. If you're inside asyncio, use async APIs (`arun`, `arun_loop`, `arun_events`).
2. Ensure toolkit loader prefixes match tool names (example: `calculator` for `calculator.*`).
3. Keep `max_rounds` high enough for tool-plan + execution + final response cycles.
4. Use `debug_mode=True` during development to inspect provider plans and tool outputs.
5. Use `stream_events=True` to build UIs with stable event contracts.
