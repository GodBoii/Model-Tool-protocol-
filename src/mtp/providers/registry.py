from __future__ import annotations

from collections.abc import Callable
from typing import Any


ProviderFactory = Callable[..., Any]

_PROVIDERS: dict[str, ProviderFactory] = {}


class ProviderRegistryError(ValueError):
    pass


def register_provider(name: str, factory: ProviderFactory, *, override: bool = False) -> None:
    key = name.strip().lower()
    if not key:
        raise ProviderRegistryError("Provider name cannot be empty.")
    if key in _PROVIDERS and not override:
        raise ProviderRegistryError(f"Provider already registered: {key}")
    _PROVIDERS[key] = factory


def create_provider(name: str, **kwargs: Any) -> Any:
    key = name.strip().lower()
    factory = _PROVIDERS.get(key)
    if factory is None:
        available = ", ".join(sorted(_PROVIDERS.keys())) or "(none)"
        raise ProviderRegistryError(
            f"Unknown provider: {key}. Registered providers: {available}"
        )
    return factory(**kwargs)


def list_providers() -> list[str]:
    return sorted(_PROVIDERS.keys())


def provider_plugin(name: str, *, override: bool = False) -> Callable[[type], type]:
    """
    Class decorator for provider plugin registration.

    Example:
        @provider_plugin("anthropic")
        class AnthropicProvider(...):
            ...
    """

    def _decorator(cls: type) -> type:
        register_provider(name, cls, override=override)
        return cls

    return _decorator

