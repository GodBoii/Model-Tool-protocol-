from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "groq": "llama-3.3-70b-versatile",
    "claude": "claude-3-5-sonnet-20241022",
    "gemini": "gemini-2.0-flash-exp",
    "openrouter": "qwen/qwen-2.5-72b-instruct",
    "mistral": "mistral-large-latest",
    "cohere": "command-r-plus-08-2024",
    "sambanova": "Meta-Llama-3.1-405B-Instruct",
    "cerebras": "llama3.1-70b",
    "deepseek": "deepseek-chat",
    "togetherai": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
    "fireworksai": "accounts/fireworks/models/llama-v3p1-70b-instruct",
    "xiaomi": "mimo-v2.5-pro",
    "ollama": "llama3.2:3b",  # Popular small model for local inference
    "lmstudio": "qwen3",  # Generic default (user will select from loaded models)
}


def provider_settings_path(session_db_path: str | Path) -> Path:
    """
    Get the path to the provider settings file.
    
    Args:
        session_db_path: Can be either a directory or a file path.
                        If it's a file, use its parent directory.
    
    Returns:
        Path to tui_provider_settings.json
    """
    base = Path(session_db_path)
    
    # If it's a file, or clearly intended to be one (for example sessions.json
    # before it has been created), use its parent directory.
    if base.is_file() or base.suffix:
        base = base.parent
    
    return base / "tui_provider_settings.json"


def _default_payload() -> dict[str, Any]:
    return {"providers": {}}


def load_provider_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_payload()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _default_payload()
    if not isinstance(payload, dict):
        return _default_payload()
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        payload["providers"] = {}
        return payload
    return payload


def save_provider_settings(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def ensure_provider_entry(payload: dict[str, Any], provider_name: str) -> dict[str, Any]:
    providers = payload.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        payload["providers"] = providers
    entry = providers.get(provider_name)
    if not isinstance(entry, dict):
        entry = {
            "api_key": None,
            "model": None,
            "models": [],
            "deployment_type": None,  # "local" or "cloud" for hybrid providers
            "base_url": None,  # Custom endpoint for local/remote deployments
            "thinking_mode": None,
            "final_thinking_mode": None,
        }
        providers[provider_name] = entry
    
    # Ensure all required fields exist
    if "api_key" not in entry:
        entry["api_key"] = None
    if "model" not in entry:
        entry["model"] = None
    if "deployment_type" not in entry:
        entry["deployment_type"] = None
    if "base_url" not in entry:
        entry["base_url"] = None
    if "thinking_mode" not in entry:
        entry["thinking_mode"] = None
    if "final_thinking_mode" not in entry:
        entry["final_thinking_mode"] = None
    
    models = entry.get("models")
    if not isinstance(models, list):
        entry["models"] = []
    else:
        cleaned: list[str] = []
        for item in models:
            if isinstance(item, str):
                candidate = item.strip()
                if candidate and candidate not in cleaned:
                    cleaned.append(candidate)
        entry["models"] = cleaned
    return entry


def preferred_model_for_provider(payload: dict[str, Any], provider_name: str) -> str:
    entry = ensure_provider_entry(payload, provider_name)
    model = entry.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return DEFAULT_PROVIDER_MODELS.get(provider_name, "gpt-4o")


def is_provider_configured(payload: dict[str, Any], provider_name: str) -> bool:
    """Check if a provider has API key and model configured."""
    from .tui_local_providers import is_local_capable_provider
    
    entry = ensure_provider_entry(payload, provider_name)
    has_model = isinstance(entry.get("model"), str) and entry["model"].strip()
    
    # Local providers don't require API keys
    if is_local_capable_provider(provider_name):
        deployment_type = entry.get("deployment_type")
        if deployment_type == "local":
            # Local deployment: only need model and base_url
            has_base_url = isinstance(entry.get("base_url"), str) and entry["base_url"].strip()
            return bool(has_model and has_base_url)
        elif deployment_type == "cloud":
            # Cloud deployment: need API key and model
            has_api_key = isinstance(entry.get("api_key"), str) and entry["api_key"].strip()
            return bool(has_api_key and has_model)
        else:
            # Not configured yet
            return False
    
    # Cloud-only providers: need API key and model
    has_api_key = isinstance(entry.get("api_key"), str) and entry["api_key"].strip()
    return bool(has_api_key and has_model)


def add_custom_model(payload: dict[str, Any], provider_name: str, model_name: str) -> bool:
    """
    Add a custom model to a provider's model list.
    
    Returns:
        True if model was added, False if it already exists.
    """
    entry = ensure_provider_entry(payload, provider_name)
    models = entry.get("models", [])
    
    if model_name in models:
        return False
    
    models.append(model_name)
    entry["models"] = models
    return True


def get_provider_models(payload: dict[str, Any], provider_name: str) -> list[str]:
    """Get all models for a provider (default + custom)."""
    entry = ensure_provider_entry(payload, provider_name)
    default_model = DEFAULT_PROVIDER_MODELS.get(provider_name)
    custom_models = entry.get("models", [])
    
    # Combine default + custom, removing duplicates
    all_models = []
    if default_model:
        all_models.append(default_model)
    if provider_name == "xiaomi" and "mimo-v2.5" not in all_models:
        all_models.append("mimo-v2.5")
    
    for model in custom_models:
        if model not in all_models:
            all_models.append(model)
    
    return all_models


def set_provider_api_key(payload: dict[str, Any], provider_name: str, api_key: str) -> None:
    """Set or update API key for a provider."""
    entry = ensure_provider_entry(payload, provider_name)
    entry["api_key"] = api_key


def delete_provider_api_key(payload: dict[str, Any], provider_name: str) -> bool:
    """
    Delete API key for a provider.
    
    Returns:
        True if key was deleted, False if no key existed.
    """
    entry = ensure_provider_entry(payload, provider_name)
    if entry.get("api_key"):
        entry["api_key"] = None
        return True
    return False


def list_configured_providers(payload: dict[str, Any]) -> list[tuple[str, bool]]:
    """
    Get list of all providers with their configuration status.
    
    Returns:
        List of (provider_name, has_api_key) tuples.
    """
    from .tui_provider_factory import SUPPORTED_TUI_PROVIDERS
    
    result = []
    for provider_name in SUPPORTED_TUI_PROVIDERS:
        entry = ensure_provider_entry(payload, provider_name)
        has_key = bool(entry.get("api_key"))
        result.append((provider_name, has_key))
    
    return result





def set_deployment_type(payload: dict[str, Any], provider_name: str, deployment_type: str) -> None:
    """Set deployment type for a provider (local or cloud)."""
    entry = ensure_provider_entry(payload, provider_name)
    entry["deployment_type"] = deployment_type


def set_base_url(payload: dict[str, Any], provider_name: str, base_url: str) -> None:
    """Set base URL for a provider."""
    entry = ensure_provider_entry(payload, provider_name)
    entry["base_url"] = base_url


def get_deployment_type(payload: dict[str, Any], provider_name: str) -> str | None:
    """Get deployment type for a provider."""
    entry = ensure_provider_entry(payload, provider_name)
    return entry.get("deployment_type")


def get_base_url(payload: dict[str, Any], provider_name: str) -> str | None:
    """Get base URL for a provider."""
    entry = ensure_provider_entry(payload, provider_name)
    return entry.get("base_url")


def set_discovered_models(payload: dict[str, Any], provider_name: str, models: list[str]) -> None:
    """Set the list of discovered models for a provider."""
    entry = ensure_provider_entry(payload, provider_name)
    entry["models"] = list(models)


def set_preferred_model(payload: dict[str, Any], provider_name: str, model: str) -> None:
    """Set the preferred model for a provider."""
    entry = ensure_provider_entry(payload, provider_name)
    entry["model"] = model
