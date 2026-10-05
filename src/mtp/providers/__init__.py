from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "AzureOpenAIResponsesToolCallingProvider": (".azure_provider", "AzureOpenAIResponsesToolCallingProvider"),
    "XAIResponsesToolCallingProvider": (".xai_provider", "XAIResponsesToolCallingProvider"),
    "BedrockConverseToolCallingProvider": (".bedrock_provider", "BedrockConverseToolCallingProvider"),
    "VertexGeminiToolCallingProvider": (".vertex_provider", "VertexGeminiToolCallingProvider"),
    "OpenAICompatibleToolCallingProvider": (".compatible_provider", "OpenAICompatibleToolCallingProvider"),
    "OpenAIResponsesToolCallingProvider": (".responses_provider", "OpenAIResponsesToolCallingProvider"),
    "HuggingFaceToolCallingProvider": (".hosted_compatible", "HuggingFaceToolCallingProvider"),
    "DeepInfraToolCallingProvider": (".hosted_compatible", "DeepInfraToolCallingProvider"),
    "DashScopeToolCallingProvider": (".hosted_compatible", "DashScopeToolCallingProvider"),
    "MockPlannerProvider": (".mock", "MockPlannerProvider"),
    "GroqToolCallingProvider": (".groq_provider", "GroqToolCallingProvider"),
    "OpenRouterToolCallingProvider": (".openrouter_provider", "OpenRouterToolCallingProvider"),
    "OpenAIToolCallingProvider": (".openai_provider", "OpenAIToolCallingProvider"),
    "LMStudioToolCallingProvider": (".lmstudio_provider", "LMStudioToolCallingProvider"),
    "OllamaToolCallingProvider": (".ollama_provider", "OllamaToolCallingProvider"),
    "GeminiToolCallingProvider": (".gemini_provider", "GeminiToolCallingProvider"),
    "AnthropicToolCallingProvider": (".anthropic_provider", "AnthropicToolCallingProvider"),
    "SambaNovaToolCallingProvider": (".sambanova_provider", "SambaNovaToolCallingProvider"),
    "CerebrasToolCallingProvider": (".cerebras_provider", "CerebrasToolCallingProvider"),
    "DeepSeekToolCallingProvider": (".deepseek_provider", "DeepSeekToolCallingProvider"),
    "MistralToolCallingProvider": (".mistral_provider", "MistralToolCallingProvider"),
    "CohereToolCallingProvider": (".cohere_provider", "CohereToolCallingProvider"),
    "TogetherAIToolCallingProvider": (".together_provider", "TogetherAIToolCallingProvider"),
    "FireworksAIToolCallingProvider": (".fireworks_provider", "FireworksAIToolCallingProvider"),
    "XiaomiToolCallingProvider": (".xiaomi_provider", "XiaomiToolCallingProvider"),
}

_ALIASES: dict[str, str] = {
    "AzureOpenAI": "AzureOpenAIResponsesToolCallingProvider",
    "XAI": "XAIResponsesToolCallingProvider",
    "Bedrock": "BedrockConverseToolCallingProvider",
    "Vertex": "VertexGeminiToolCallingProvider",
    "OpenAICompatible": "OpenAICompatibleToolCallingProvider",
    "OpenAIResponses": "OpenAIResponsesToolCallingProvider",
    "HuggingFace": "HuggingFaceToolCallingProvider",
    "DeepInfra": "DeepInfraToolCallingProvider",
    "DashScope": "DashScopeToolCallingProvider",
    "Groq": "GroqToolCallingProvider",
    "OpenRouter": "OpenRouterToolCallingProvider",
    "OpenAI": "OpenAIToolCallingProvider",
    "LMStudio": "LMStudioToolCallingProvider",
    "Ollama": "OllamaToolCallingProvider",
    "Gemini": "GeminiToolCallingProvider",
    "Anthropic": "AnthropicToolCallingProvider",
    "SambaNova": "SambaNovaToolCallingProvider",
    "Cerebras": "CerebrasToolCallingProvider",
    "DeepSeek": "DeepSeekToolCallingProvider",
    "Mistral": "MistralToolCallingProvider",
    "Cohere": "CohereToolCallingProvider",
    "TogetherAI": "TogetherAIToolCallingProvider",
    "FireworksAI": "FireworksAIToolCallingProvider",
    "Xiaomi": "XiaomiToolCallingProvider",
}

__all__ = sorted([*_EXPORTS.keys(), *_ALIASES.keys()])


def _load(name: str) -> Any:
    target = _ALIASES.get(name, name)
    module_name, class_name = _EXPORTS[target]
    module = import_module(module_name, package=__name__)
    cls = getattr(module, class_name)
    globals()[target] = cls
    if name in _ALIASES:
        globals()[name] = cls
    return cls


def __getattr__(name: str) -> Any:
    if name in _EXPORTS or name in _ALIASES:
        return _load(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
