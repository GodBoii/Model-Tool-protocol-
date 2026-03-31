# Providers

MTP uses explicit provider classes.  
You instantiate the provider you want and pass it to `Agent` / `MTPAgent`.

## Built-in usage (explicit)

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
```

## 2) Export provider class

In `src/mtp/providers/__init__.py`:

```python
from .anthropic_provider import AnthropicToolCallingProvider
```

## 3) Use provider directly

```python
from mtp import MTPAgent, ToolRegistry
from mtp.providers import AnthropicToolCallingProvider

provider = AnthropicToolCallingProvider(model="claude-...")
registry = ToolRegistry()
agent = MTPAgent(provider=provider, registry=registry)
```

## Notes

- This matches the explicit style used by frameworks like Agno.
- No provider is defaulted by core `Agent` / `MTPAgent`.
- Different providers can expose different constructor parameters safely.
