# Providers

MTP supports both:
- short ergonomic aliases (Agno-style), for example `Groq`
- explicit provider class names, for example `GroqToolCallingProvider`

Both styles are equivalent.

## Built-in usage (alias style)

```python
from mtp.providers import Groq

provider = Groq(model="llama-3.3-70b-versatile")
```

## Built-in usage (explicit style)

```python
from mtp.providers import GroqToolCallingProvider

provider = GroqToolCallingProvider(model="llama-3.3-70b-versatile")
```

## Add a new provider

## 1) Create provider file

Example: `src/mtp/providers/anthropic_provider.py`

```python
from mtp.agent import AgentAction, ProviderAdapter

class AnthropicToolCallingProvider(ProviderAdapter):
    def next_action(self, messages, tools) -> AgentAction:
        ...

    def finalize(self, messages, tool_results) -> str:
        ...

    async def anext_action(self, messages, tools) -> AgentAction:
        ...

    async def afinalize(self, messages, tool_results) -> str:
        ...
```

## 2) Export provider class

In `src/mtp/providers/__init__.py`:

```python
from .anthropic_provider import AnthropicToolCallingProvider
```

## 3) Use provider directly

```python
from mtp import Agent
from mtp.providers import AnthropicToolCallingProvider

provider = AnthropicToolCallingProvider(model="claude-...")
registry = Agent.ToolRegistry()
agent = Agent.MTPAgent(provider=provider, tools=registry)
```

## Notes

- Alias names available (when matching optional SDKs are installed):
  - `Groq`, `OpenRouter`, `OpenAI`, `Gemini`, `Anthropic`, `SambaNova`
  - `Cerebras`, `DeepSeek`, `Mistral`, `Cohere`, `TogetherAI`, `FireworksAI`
- Local deterministic planner provider is also available as `MockPlannerProvider` (class alias for `SimplePlannerProvider`).
- Provider exports are dependency-optional: missing SDKs no longer block importing other providers.
- Explicit class names remain fully supported and unchanged.
- No provider is defaulted by core `Agent` / `MTPAgent`.
- Different providers can expose different constructor parameters safely.
- Async provider hooks are optional. If omitted, async agent APIs fall back to running sync provider methods in threads.

Related:
- [Storage and Sessions](C:\Users\prajw\Downloads\MTP\docs\STORAGE.md)
