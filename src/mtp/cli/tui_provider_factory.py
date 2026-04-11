from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mtp.providers import Anthropic, Gemini, Groq, OpenAI, OpenRouter


SUPPORTED_TUI_PROVIDERS: tuple[str, ...] = (
    "openai",
    "groq",
    "claude",
    "openrouter",
    "gemini",
)

_PROVIDER_ALIASES: dict[str, str] = {
    "anthropic": "claude",
}


def normalize_tui_provider(value: str) -> str:
    normalized = value.strip().lower()
    normalized = _PROVIDER_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_TUI_PROVIDERS:
        supported = ", ".join(SUPPORTED_TUI_PROVIDERS)
        raise ValueError(f"Unsupported provider: {value!r}. Supported values: {supported}")
    return normalized


def mask_api_key(value: str | None) -> str:
    if value is None:
        return "(env)"
    cleaned = value.strip()
    if not cleaned:
        return "(empty)"
    if len(cleaned) <= 8:
        return "*" * len(cleaned)
    return f"{cleaned[:4]}...{cleaned[-4:]}"


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    provider_name: str
    model_name: str
    api_key: str | None


_ProviderBuilder = Callable[[str, str | None], Any]


def _openai_builder(model: str, api_key: str | None) -> Any:
    return OpenAI(model=model, api_key=api_key)


def _groq_builder(model: str, api_key: str | None) -> Any:
    return Groq(model=model, api_key=api_key)


def _claude_builder(model: str, api_key: str | None) -> Any:
    return Anthropic(model=model, api_key=api_key)


def _openrouter_builder(model: str, api_key: str | None) -> Any:
    return OpenRouter(model=model, api_key=api_key)


def _gemini_builder(model: str, api_key: str | None) -> Any:
    return Gemini(model=model, api_key=api_key)


PROVIDER_BUILDERS: dict[str, _ProviderBuilder] = {
    "openai": _openai_builder,
    "groq": _groq_builder,
    "claude": _claude_builder,
    "openrouter": _openrouter_builder,
    "gemini": _gemini_builder,
}


def build_tui_provider(selection: ProviderSelection) -> Any:
    provider_name = normalize_tui_provider(selection.provider_name)
    builder = PROVIDER_BUILDERS[provider_name]
    return builder(selection.model_name, selection.api_key)
