from __future__ import annotations

import json
import os
import tempfile
import warnings
from pathlib import Path
from typing import Any

from ..model_catalog import DEFAULT_MODEL_BY_PROVIDER


DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "groq": "llama-3.3-70b-versatile",
    "claude": DEFAULT_MODEL_BY_PROVIDER["claude"],
    "gemini": DEFAULT_MODEL_BY_PROVIDER["gemini"],
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


KEYRING_SERVICE = "mtpx.provider-api-key"

PROVIDER_API_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "sambanova": "SAMBANOVA_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "togetherai": "TOGETHER_API_KEY",
    "fireworksai": "FIREWORKS_API_KEY",
    "xiaomi": "MIMO_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "lmstudio": "LMSTUDIO_API_KEY",
}


class CredentialStorageError(RuntimeError):
    """Raised when a provider credential cannot be stored safely."""


def provider_api_key_env(provider_name: str) -> str:
    """Return the documented environment variable for a provider credential."""
    normalized = provider_name.strip().lower()
    return PROVIDER_API_KEY_ENV.get(normalized, f"{normalized.upper()}_API_KEY")


def _credential_storage_help(provider_name: str) -> str:
    env_name = provider_api_key_env(provider_name)
    return (
        "The operating-system credential store is unavailable. Install a working "
        "keyring backend (for example `pip install keyring`) or set "
        f"{env_name} in your environment. MTPX will not write the API key to JSON."
    )


def _keyring() -> Any:
    try:
        import keyring
    except ImportError as exc:
        raise CredentialStorageError(_credential_storage_help("provider")) from exc
    try:
        backend = keyring.get_keyring()
        if float(getattr(backend, "priority", 0)) <= 0:
            raise CredentialStorageError(_credential_storage_help("provider"))
    except CredentialStorageError:
        raise
    except Exception as exc:
        raise CredentialStorageError(_credential_storage_help("provider")) from exc
    return keyring


def _read_keyring_api_key(provider_name: str) -> str | None:
    try:
        value = _keyring().get_password(KEYRING_SERVICE, provider_name)
    except CredentialStorageError:
        return None
    except Exception:
        # Reading settings should remain usable when a desktop keychain is
        # temporarily locked. Explicit writes still fail with guidance.
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _store_keyring_api_key(provider_name: str, api_key: str) -> None:
    try:
        _keyring().set_password(KEYRING_SERVICE, provider_name, api_key)
    except CredentialStorageError as exc:
        raise CredentialStorageError(_credential_storage_help(provider_name)) from exc
    except Exception as exc:
        raise CredentialStorageError(_credential_storage_help(provider_name)) from exc


def _delete_keyring_api_key(provider_name: str) -> bool:
    keyring = _keyring()
    try:
        keyring.delete_password(KEYRING_SERVICE, provider_name)
        return True
    except Exception as exc:
        # Missing credentials are normal and keyring exposes backend-specific
        # exception classes, so confirm with a safe read before reporting failure.
        try:
            if keyring.get_password(KEYRING_SERVICE, provider_name) is None:
                return False
        except Exception:
            pass
        raise CredentialStorageError(_credential_storage_help(provider_name)) from exc


def _resolve_provider_api_key(provider_name: str) -> tuple[str | None, str | None]:
    key = _read_keyring_api_key(provider_name)
    if key:
        return key, "keyring"
    env_key = os.getenv(provider_api_key_env(provider_name))
    if isinstance(env_key, str) and env_key.strip():
        return env_key.strip(), "environment"
    return None, None


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
    migrated = False
    for provider_name, raw_entry in providers.items():
        if not isinstance(provider_name, str) or not isinstance(raw_entry, dict):
            continue
        legacy_key = raw_entry.get("api_key")
        if isinstance(legacy_key, str) and legacy_key.strip():
            try:
                _store_keyring_api_key(provider_name, legacy_key.strip())
            except CredentialStorageError:
                # Do not destroy the only copy. It remains usable for this run,
                # and any attempted save will fail safely until it can migrate.
                raw_entry["_api_key_source"] = "legacy-plaintext"
                warnings.warn(
                    f"Legacy plaintext credential for {provider_name} could not be migrated. "
                    + _credential_storage_help(provider_name),
                    RuntimeWarning,
                    stacklevel=2,
                )
            else:
                raw_entry["api_key"] = legacy_key.strip()
                raw_entry["_api_key_source"] = "keyring"
                migrated = True
            continue
        key, source = _resolve_provider_api_key(provider_name)
        raw_entry["api_key"] = key
        raw_entry["_api_key_source"] = source
    if migrated:
        save_provider_settings(path, payload)
    return payload


def save_provider_settings(path: Path, payload: dict[str, Any]) -> None:
    # Never serialize credentials or internal hydration metadata. A direct
    # assignment from older callers is treated as a legacy credential and must
    # be secured before the non-secret settings are written.
    serializable = {key: value for key, value in payload.items() if not str(key).startswith("_")}
    serializable = json.loads(json.dumps(serializable))
    providers = serializable.get("providers")
    source_providers = payload.get("providers")
    if isinstance(providers, dict):
        for provider_name, entry in providers.items():
            if not isinstance(entry, dict):
                continue
            source_entry = source_providers.get(provider_name) if isinstance(source_providers, dict) else None
            source = source_entry.get("_api_key_source") if isinstance(source_entry, dict) else None
            api_key = entry.pop("api_key", None)
            for key in tuple(entry):
                if str(key).startswith("_"):
                    entry.pop(key, None)
            if isinstance(api_key, str) and api_key.strip() and source not in {"keyring", "environment"}:
                _store_keyring_api_key(provider_name, api_key.strip())
                if isinstance(source_entry, dict):
                    source_entry["_api_key_source"] = "keyring"
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(serializable, indent=2, ensure_ascii=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def mask_api_key(api_key: str) -> str:
    """Return a display-safe representation that never reveals the full secret."""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


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
        api_key, source = _resolve_provider_api_key(provider_name)
        entry["api_key"] = api_key
        entry["_api_key_source"] = source
    
    # Ensure all required fields exist
    if "api_key" not in entry:
        api_key, source = _resolve_provider_api_key(provider_name)
        entry["api_key"] = api_key
        entry["_api_key_source"] = source
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
    """Store a provider key in the OS credential vault and hydrate the payload."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API key cannot be empty")
    _store_keyring_api_key(provider_name, api_key)
    entry = ensure_provider_entry(payload, provider_name)
    entry["api_key"] = api_key
    entry["_api_key_source"] = "keyring"


def delete_provider_api_key(payload: dict[str, Any], provider_name: str) -> bool:
    """
    Delete API key for a provider.
    
    Returns:
        True if key was deleted, False if no key existed.
    """
    entry = ensure_provider_entry(payload, provider_name)
    source = entry.get("_api_key_source")
    if source == "environment":
        return False
    deleted = False
    if source == "keyring":
        deleted = _delete_keyring_api_key(provider_name)
    elif source == "legacy-plaintext":
        deleted = bool(entry.get("api_key"))
    entry["api_key"] = None
    entry["_api_key_source"] = None
    return deleted


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
