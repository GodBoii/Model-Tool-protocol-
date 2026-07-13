from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from typing import Any

from .tui_settings import provider_credential_status


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    name: str
    alias: str
    class_name: str
    sdk_module: str | None
    env_var: str | None
    notes: str = ""

    def sdk_installed(self) -> bool | None:
        if self.sdk_module is None:
            return None
        return importlib.util.find_spec(self.sdk_module) is not None


PROVIDERS: list[ProviderInfo] = [
    ProviderInfo("mock", "MockPlannerProvider", "MockPlannerProvider", None, None, "local deterministic planner"),
    ProviderInfo("groq", "Groq", "GroqToolCallingProvider", "groq", "GROQ_API_KEY"),
    ProviderInfo("openai", "OpenAI", "OpenAIToolCallingProvider", "openai", "OPENAI_API_KEY"),
    ProviderInfo("openrouter", "OpenRouter", "OpenRouterToolCallingProvider", "openai", "OPENROUTER_API_KEY"),
    ProviderInfo("anthropic", "Anthropic", "AnthropicToolCallingProvider", "anthropic", "ANTHROPIC_API_KEY"),
    ProviderInfo("gemini", "Gemini", "GeminiToolCallingProvider", "google.genai", "GEMINI_API_KEY"),
    ProviderInfo("sambanova", "SambaNova", "SambaNovaToolCallingProvider", "openai", "SAMBANOVA_API_KEY"),
    ProviderInfo("cerebras", "Cerebras", "CerebrasToolCallingProvider", "openai", "CEREBRAS_API_KEY"),
    ProviderInfo("deepseek", "DeepSeek", "DeepSeekToolCallingProvider", "openai", "DEEPSEEK_API_KEY"),
    ProviderInfo("mistral", "Mistral", "MistralToolCallingProvider", "mistralai", "MISTRAL_API_KEY"),
    ProviderInfo("cohere", "Cohere", "CohereToolCallingProvider", "cohere", "COHERE_API_KEY"),
    ProviderInfo("togetherai", "TogetherAI", "TogetherAIToolCallingProvider", "openai", "TOGETHER_API_KEY"),
    ProviderInfo("fireworksai", "FireworksAI", "FireworksAIToolCallingProvider", "openai", "FIREWORKS_API_KEY"),
    ProviderInfo("xiaomi", "Xiaomi", "XiaomiToolCallingProvider", "openai", "MIMO_API_KEY", "MiMo OpenAI-compatible Token Plan endpoint"),
    ProviderInfo("ollama", "Ollama", "OllamaToolCallingProvider", "ollama", None, "local inference (localhost:11434)"),
    ProviderInfo("lmstudio", "LMStudio", "LMStudioToolCallingProvider", "openai", None, "local inference (localhost:1234)"),
]


def get_provider(name_or_alias: str) -> ProviderInfo | None:
    needle = name_or_alias.strip().lower()
    for info in PROVIDERS:
        if info.name == needle:
            return info
        if info.alias.lower() == needle:
            return info
        if info.class_name.lower() == needle:
            return info
    return None


def list_providers() -> list[ProviderInfo]:
    return list(PROVIDERS)


def provider_as_row(info: ProviderInfo) -> dict[str, Any]:
    """Return operational metadata without ever exposing credential values."""
    sdk_installed = info.sdk_installed()
    sdk_status = "built-in" if sdk_installed is None else ("installed" if sdk_installed else "missing")

    if info.env_var is None:
        key_status = "not-required"
        key_configured = None
        key_source = None
    else:
        key_configured, key_source = provider_credential_status(
            info.name, env_var=info.env_var
        )
        key_status = "configured" if key_configured else "missing"

    ready = sdk_installed is not False and key_configured is not False
    return {
        "name": info.name,
        "alias": info.alias,
        "class": info.class_name,
        "sdk": info.sdk_module or "-",
        "sdk_status": sdk_status,
        "env": info.env_var or "-",
        "key_status": key_status,
        "key_source": key_source or "-",
        "ready": ready,
        "notes": info.notes,
    }


def providers_as_rows() -> list[dict[str, Any]]:
    return [provider_as_row(info) for info in PROVIDERS]
