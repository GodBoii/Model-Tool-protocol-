"""Shared SDK and CLI defaults. Explicit saved selections are never replaced."""

DEFAULT_PROVIDER_MODELS = {
    "huggingface": "openai/gpt-oss-120b:cerebras",
    "deepinfra": "deepseek-ai/DeepSeek-V4-Flash-0731",
    "dashscope": "qwen-plus",
    "openai": "gpt-4o",
    "openai_responses": "gpt-4o",
    "groq": "qwen/qwen3.8-27b",
    "anthropic": "claude-sonnet-5-5",
    "gemini": "gemini-3.8-flash",
    "openrouter": "qwen/qwen-2.5-72b-instruct",
    "mistral": "mistral-large-latest",
    "cohere": "command-a-plus-05-2026",
    "sambanova": "Meta-Llama-3.3-70B-Instruct",
    "cerebras": "qwen-3.8-27b",
    "deepseek": "deepseek-chat",
    "togetherai": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "fireworksai": "accounts/fireworks/models/llama-v3p3-70b-instruct",
    "xiaomi": "mimo-v2.5-pro",
    "ollama": "qwen3",
    "lmstudio": "qwen3",
}
