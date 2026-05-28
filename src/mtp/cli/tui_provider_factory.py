from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# Lazy imports to avoid requiring all provider SDKs
# Providers are imported on-demand when actually used


SUPPORTED_TUI_PROVIDERS: tuple[str, ...] = (
    "openai",
    "groq",
    "claude",
    "gemini",
    "openrouter",
    "mistral",
    "cohere",
    "sambanova",
    "cerebras",
    "deepseek",
    "togetherai",
    "fireworksai",
    "xiaomi",
    "ollama",      # Local inference support
    "lmstudio",    # Local inference support
)

_PROVIDER_ALIASES: dict[str, str] = {
    "anthropic": "claude",
    "together": "togetherai",
    "fireworks": "fireworksai",
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
    base_url: str | None = None  # For local providers
    provider_options: dict[str, Any] | None = None


_ProviderBuilder = Callable[[str, str | None, str | None, dict[str, Any] | None], Any]


def _openai_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import OpenAI
    return OpenAI(model=model, api_key=api_key)


def _groq_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import Groq
    return Groq(model=model, api_key=api_key)


def _claude_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import Anthropic
    return Anthropic(model=model, api_key=api_key)


def _openrouter_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import OpenRouter
    return OpenRouter(model=model, api_key=api_key)


def _gemini_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import Gemini
    return Gemini(model=model, api_key=api_key)


def _mistral_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import Mistral
    return Mistral(model=model, api_key=api_key)


def _cohere_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import Cohere
    return Cohere(model=model, api_key=api_key)


def _sambanova_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import SambaNova
    return SambaNova(model=model, api_key=api_key)


def _cerebras_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import Cerebras
    return Cerebras(model=model, api_key=api_key)


def _deepseek_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import DeepSeek
    return DeepSeek(model=model, api_key=api_key)


def _togetherai_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import TogetherAI
    return TogetherAI(model=model, api_key=api_key)


def _fireworksai_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import FireworksAI
    return FireworksAI(model=model, api_key=api_key)


def _xiaomi_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import Xiaomi
    options = provider_options or {}
    thinking_mode = options.get("thinking_mode") or "adaptive"
    final_thinking_mode = options.get("final_thinking_mode")
    if not isinstance(final_thinking_mode, str) or not final_thinking_mode.strip():
        final_thinking_mode = thinking_mode if thinking_mode in {"enabled", "disabled"} else "enabled"
    return Xiaomi(
        model=model,
        api_key=api_key,
        base_url=base_url or "https://token-plan-ams.xiaomimimo.com/v1",
        thinking_mode=thinking_mode,
        final_thinking_mode=final_thinking_mode,
    )


def _ollama_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import Ollama
    
    # Use provided base_url or default
    host = base_url or "http://localhost:11434"
    
    return Ollama(
        model=model,
        host=host,
        api_key=api_key,  # Optional for cloud deployments
        think=True,
        options={"temperature": 0},
    )


def _lmstudio_builder(model: str, api_key: str | None, base_url: str | None, provider_options: dict[str, Any] | None = None) -> Any:
    from mtp.providers import LMStudio
    
    # Use provided base_url or default
    endpoint = base_url or "http://127.0.0.1:1234/v1"
    
    return LMStudio(
        model=model,
        base_url=endpoint,
        api_key=api_key or "lm-studio",  # LM Studio requires a dummy key
        temperature=0.0,
        parallel_tool_calls=True,
    )


PROVIDER_BUILDERS: dict[str, _ProviderBuilder] = {
    "openai": _openai_builder,
    "groq": _groq_builder,
    "claude": _claude_builder,
    "openrouter": _openrouter_builder,
    "gemini": _gemini_builder,
    "mistral": _mistral_builder,
    "cohere": _cohere_builder,
    "sambanova": _sambanova_builder,
    "cerebras": _cerebras_builder,
    "deepseek": _deepseek_builder,
    "togetherai": _togetherai_builder,
    "fireworksai": _fireworksai_builder,
    "xiaomi": _xiaomi_builder,
    "ollama": _ollama_builder,
    "lmstudio": _lmstudio_builder,
}


def build_tui_provider(selection: ProviderSelection) -> Any:
    provider_name = normalize_tui_provider(selection.provider_name)
    builder = PROVIDER_BUILDERS[provider_name]
    
    try:
        return builder(selection.model_name, selection.api_key, selection.base_url, selection.provider_options)
    except ImportError as e:
        # Provider SDK not installed
        module_name = str(e).split("'")[1] if "'" in str(e) else "unknown"
        raise ImportError(
            f"Provider '{provider_name}' requires the '{module_name}' package. "
            f"Install it with: pip install 'mtpx[{provider_name}]'"
        ) from e
