# Providers

MTP uses a provider registry plugin pattern so new providers are easy to add and discover.

## Built-in usage

```python
from mtp import create_provider, list_providers

print(list_providers())  # e.g. ["groq", "mock"]
provider = create_provider("groq", model="llama-3.3-70b-versatile")
```

## Add a new provider (1 file + 1 registration line)

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

## 2) Register provider

In `src/mtp/providers/__init__.py`:

```python
from .anthropic_provider import AnthropicToolCallingProvider
from .registry import register_provider, list_providers

if "anthropic" not in list_providers():
    register_provider("anthropic", AnthropicToolCallingProvider)
```

That is the required registration line.

## Optional decorator style

```python
from mtp.providers import provider_plugin

@provider_plugin("anthropic")
class AnthropicToolCallingProvider(...):
    ...
```

## Registry API

- `register_provider(name, factory, override=False)`
- `create_provider(name, **kwargs)`
- `list_providers()`
- `provider_plugin(name, override=False)`

## Notes

- Registry is provider-agnostic. No provider is mandatory for core Agent.
- `MTPAgent` stays explicit: user supplies `provider` + `registry`.
