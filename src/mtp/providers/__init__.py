from __future__ import annotations

from importlib import import_module


def _try_register(
    *,
    module_name: str,
    class_name: str,
    alias: str | None = None,
) -> None:
    try:
        module = import_module(module_name, package=__name__)
    except Exception:
        return
    cls = getattr(module, class_name, None)
    if cls is None:
        return
    globals()[class_name] = cls
    __all__.append(class_name)
    if alias:
        globals()[alias] = cls
        __all__.append(alias)


__all__: list[str] = []

_try_register(module_name=".mock", class_name="MockPlannerProvider")
_try_register(module_name=".groq_provider", class_name="GroqToolCallingProvider", alias="Groq")
_try_register(module_name=".openrouter_provider", class_name="OpenRouterToolCallingProvider", alias="OpenRouter")
_try_register(module_name=".openai_provider", class_name="OpenAIToolCallingProvider", alias="OpenAI")
_try_register(module_name=".gemini_provider", class_name="GeminiToolCallingProvider", alias="Gemini")
_try_register(module_name=".anthropic_provider", class_name="AnthropicToolCallingProvider", alias="Anthropic")
_try_register(module_name=".sambanova_provider", class_name="SambaNovaToolCallingProvider", alias="SambaNova")
_try_register(module_name=".cerebras_provider", class_name="CerebrasToolCallingProvider", alias="Cerebras")
_try_register(module_name=".deepseek_provider", class_name="DeepSeekToolCallingProvider", alias="DeepSeek")
_try_register(module_name=".mistral_provider", class_name="MistralToolCallingProvider", alias="Mistral")
_try_register(module_name=".cohere_provider", class_name="CohereToolCallingProvider", alias="Cohere")
_try_register(module_name=".together_provider", class_name="TogetherAIToolCallingProvider", alias="TogetherAI")
_try_register(module_name=".fireworks_provider", class_name="FireworksAIToolCallingProvider", alias="FireworksAI")
