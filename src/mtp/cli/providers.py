from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from typing import Any


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
    ProviderInfo("mistral", "Mistral", "MistralToolCallingProvider", "openai", "MISTRAL_API_KEY"),
    ProviderInfo("cohere", "Cohere", "CohereToolCallingProvider", "cohere", "COHERE_API_KEY"),
    ProviderInfo("togetherai", "TogetherAI", "TogetherAIToolCallingProvider", "openai", "TOGETHER_API_KEY"),
    ProviderInfo("fireworksai", "FireworksAI", "FireworksAIToolCallingProvider", "openai", "FIREWORKS_API_KEY"),
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


def providers_as_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for info in PROVIDERS:
        sdk_installed = info.sdk_installed()
        sdk_status = "-"
        if sdk_installed is True:
            sdk_status = "installed"
        elif sdk_installed is False:
            sdk_status = "missing"
        rows.append(
            {
                "name": info.name,
                "alias": info.alias,
                "class": info.class_name,
                "sdk": info.sdk_module or "-",
                "sdk_status": sdk_status,
                "env": info.env_var or "-",
                "notes": info.notes,
            }
        )
    return rows

