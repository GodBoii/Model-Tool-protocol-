"""
Model Context Window Configuration

This module manages context window sizes for different models and providers.
"""

from __future__ import annotations

from typing import Dict, Tuple


# Known context windows for various models (in tokens)
MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    # OpenAI models
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    
    # Anthropic models
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    
    # Google models
    "gemini-2.0-flash-exp": 1_000_000,
    "gemini-1.5-pro": 2_000_000,
    "gemini-1.5-flash": 1_000_000,
    
    # Groq models
    "llama-3.3-70b-versatile": 128_000,
    "llama-3.1-70b-versatile": 128_000,
    "llama-3.1-8b-instant": 128_000,
    "mixtral-8x7b-32768": 32_768,
    
    # Ollama models (common defaults)
    "llama3.2:3b": 128_000,
    "llama3.2:1b": 128_000,
    "llama3.1:8b": 128_000,
    "llama3.1:70b": 128_000,
    "qwen3:1.7b": 32_768,
    "qwen3:4b": 32_768,
    "qwen3:8b": 32_768,
    "qwen2.5:7b": 128_000,
    "qwen2.5:14b": 128_000,
    "qwen2.5:32b": 128_000,
    "qwen2.5:72b": 128_000,
    "mistral:7b": 32_768,
    "mistral:latest": 32_768,
    "codellama:7b": 16_384,
    "codellama:13b": 16_384,
    "codellama:34b": 16_384,
    "deepseek-coder:6.7b": 16_384,
    "deepseek-coder:33b": 16_384,
    "deepseek-r1:1.5b": 65_536,
    "deepseek-r1:7b": 65_536,
    "deepseek-r1:8b": 65_536,
    "deepseek-r1:14b": 65_536,
    "deepseek-r1:32b": 65_536,
    "deepseek-r1:70b": 65_536,
    "deepseek-r1:671b": 65_536,
    "phi3:3.8b": 128_000,
    "phi3:14b": 128_000,
    "gemma2:2b": 8_192,
    "gemma2:9b": 8_192,
    "gemma2:27b": 8_192,
    
    # LM Studio models (common defaults - actual depends on loaded model)
    "Meta-Llama-3-8B-Instruct-GGUF": 8_192,
    "lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF": 8_192,
    "qwen3-4b-thinking-2507": 32_768,
    "llama-3.1-8b": 128_000,
    "llama-3.1-70b": 128_000,
    "mistral-7b": 32_768,
    "codellama-7b": 16_384,
    "codellama-13b": 16_384,
}

# Default context windows by provider (fallback if model not found)
PROVIDER_DEFAULT_CONTEXT: Dict[str, int] = {
    "openai": 128_000,
    "anthropic": 200_000,
    "claude": 200_000,
    "gemini": 1_000_000,
    "groq": 128_000,
    "ollama": 32_768,  # Conservative default for local models
    "lmstudio": 32_768,  # Conservative default for local models
    "openrouter": 128_000,
    "mistral": 128_000,
    "cohere": 128_000,
    "sambanova": 128_000,
    "cerebras": 128_000,
    "deepseek": 128_000,
    "togetherai": 128_000,
    "fireworksai": 128_000,
}


def get_context_window(provider: str | None, model: str | None) -> Tuple[int, str]:
    """
    Get context window size for a model.
    
    Args:
        provider: Provider name (e.g., "ollama", "openai")
        model: Model name (e.g., "llama3.2:3b", "gpt-4o")
    
    Returns:
        Tuple of (context_window_size, source)
        source can be: "model_exact", "model_fuzzy", "provider_default", "global_default"
    """
    # Try exact model match
    if model:
        model_lower = model.lower().strip()
        if model_lower in MODEL_CONTEXT_WINDOWS:
            return MODEL_CONTEXT_WINDOWS[model_lower], "model_exact"
        
        # Try fuzzy match (e.g., "llama3.2:3b-q4_0" matches "llama3.2:3b")
        # Check both directions: known_model in provided_model AND provided_model in known_model
        for known_model, context_size in MODEL_CONTEXT_WINDOWS.items():
            # Normalize both for comparison (remove special chars, lowercase)
            known_normalized = known_model.lower().replace("-", "").replace("_", "").replace(":", "").replace("/", "")
            model_normalized = model_lower.replace("-", "").replace("_", "").replace(":", "").replace("/", "")
            
            # Check if they match (either direction)
            if (known_normalized in model_normalized or 
                model_normalized in known_normalized or
                model_lower.startswith(known_model) or
                known_model in model_lower):
                return context_size, "model_fuzzy"
    
    # Try provider default
    if provider:
        provider_lower = provider.lower().strip()
        if provider_lower in PROVIDER_DEFAULT_CONTEXT:
            return PROVIDER_DEFAULT_CONTEXT[provider_lower], "provider_default"
    
    # Global fallback
    return 128_000, "global_default"


def format_context_usage(
    used_tokens: int,
    provider: str | None,
    model: str | None,
) -> Tuple[str, float]:
    """
    Format context usage string with percentage.
    
    Args:
        used_tokens: Number of tokens used
        provider: Provider name
        model: Model name
    
    Returns:
        Tuple of (formatted_string, percentage)
    """
    context_window, source = get_context_window(provider, model)
    
    percentage = (used_tokens / context_window * 100) if context_window > 0 else 0.0
    
    formatted = f"{used_tokens:,}/{context_window:,}"
    
    return formatted, percentage


def add_custom_context_window(model: str, context_size: int) -> None:
    """
    Add a custom context window size for a model.
    
    Args:
        model: Model name
        context_size: Context window size in tokens
    """
    MODEL_CONTEXT_WINDOWS[model.lower().strip()] = context_size


def get_all_known_models() -> Dict[str, int]:
    """Get all known model context windows."""
    return dict(MODEL_CONTEXT_WINDOWS)
