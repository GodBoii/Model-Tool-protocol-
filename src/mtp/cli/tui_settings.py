from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "groq": "openai/gpt-oss-120b",
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

PROVIDER_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY", "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY", "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY", "sambanova": "SAMBANOVA_API_KEY", "cerebras": "CEREBRAS_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY", "togetherai": "TOGETHER_API_KEY", "fireworksai": "FIREWORKS_API_KEY",
    "xiaomi": "MIMO_API_KEY", "ollama": "OLLAMA_API_KEY", "lmstudio": "LMSTUDIO_API_KEY",
}


def provider_api_key(payload: dict[str, Any], provider_name: str) -> str | None:
    """Resolve a saved key first, then the provider's environment variable."""
    saved = ensure_provider_entry(payload, provider_name).get("api_key")
    if isinstance(saved, str) and saved.strip() and not any(c.isspace() or ord(c) < 32 for c in saved.strip()):
        return saved.strip()
    env_name = PROVIDER_KEY_ENV.get(provider_name)
    value = os.getenv(env_name, "").strip() if env_name else ""
    return value if value and not any(c.isspace() or ord(c) < 32 for c in value) else None


def provider_setup_status(payload: dict[str, Any], provider_name: str) -> str:
    """Describe readiness without exposing credential content."""
    if provider_name == "codex":
        return "Uses Codex CLI login"
    if provider_name in {"ollama", "lmstudio"}:
        return "Endpoint configured" if is_provider_configured(payload, provider_name) else "Needs endpoint setup"
    entry = ensure_provider_entry(payload, provider_name)
    saved = entry.get("api_key")
    if isinstance(saved, str) and saved.strip() and provider_api_key(payload, provider_name) == saved.strip():
        return "Key saved locally"
    if provider_api_key(payload, provider_name):
        return f"Key from {PROVIDER_KEY_ENV[provider_name]}"
    return "Needs API key"


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


class _SettingsCache:
    """Parsed settings keyed by path and file signature.

    The TUI reads settings on every status refresh. A stat is much cheaper
    than read + parse, and the signature check picks up edits made by other
    processes. Callers mutate what they get back, so they receive copies.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[Path, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._recoveries: list[SettingsRecovery] = []

    @staticmethod
    def signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def get(self, path: Path, signature: tuple[int, int]) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(path)
        if entry is None or entry[0] != signature:
            return None
        return copy.deepcopy(entry[1])

    def put(self, path: Path, signature: tuple[int, int] | None, payload: dict[str, Any]) -> None:
        with self._lock:
            if signature is None:
                self._entries.pop(path, None)
            else:
                self._entries[path] = (signature, copy.deepcopy(payload))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._recoveries.clear()

    def add_recovery(self, recovery: SettingsRecovery) -> None:
        with self._lock:
            self._recoveries.append(recovery)

    def pop_recoveries(self) -> list[SettingsRecovery]:
        with self._lock:
            recoveries, self._recoveries = self._recoveries, []
        return recoveries


@dataclass(frozen=True, slots=True)
class SettingsRecovery:
    """A settings file that could not be parsed and was backed up."""

    path: Path
    backup_path: Path | None
    reason: str

    def message(self) -> str:
        if self.backup_path is None:
            return f"{self.path.name} is unreadable ({self.reason}) and could not be backed up. Starting with empty settings."
        return (
            f"{self.path.name} was unreadable ({self.reason}). "
            f"Saved a copy as {self.backup_path.name}; starting with empty settings."
        )


class _CorruptSettings(ValueError):
    pass


_SETTINGS_CACHE = _SettingsCache()


def _parse_settings(raw: str) -> dict[str, Any]:
    """Parse settings JSON. Raises ``_CorruptSettings`` if it is not usable."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _CorruptSettings(f"invalid JSON at line {exc.lineno}") from exc
    if not isinstance(payload, dict):
        raise _CorruptSettings("top level is not an object")
    if "providers" not in payload:
        payload["providers"] = {}
    elif not isinstance(payload["providers"], dict):
        raise _CorruptSettings("'providers' is not an object")
    return payload


def _back_up_corrupt_file(path: Path) -> Path | None:
    """Copy ``path`` next to itself so the user's keys can be recovered by hand."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
    suffix = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.corrupt-{stamp}-{suffix}")
        suffix += 1
    try:
        shutil.copy2(path, backup)
    except OSError:
        return None
    return backup


def pop_settings_recoveries() -> list[SettingsRecovery]:
    """Return and clear notices about corrupt settings files that were backed up."""
    return _SETTINGS_CACHE.pop_recoveries()


def load_provider_settings(path: Path) -> dict[str, Any]:
    path = Path(path)
    signature = _SETTINGS_CACHE.signature(path)
    if signature is None:
        return _default_payload()
    cached = _SETTINGS_CACHE.get(path, signature)
    if cached is not None:
        return cached
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return _default_payload()
    try:
        payload = _parse_settings(raw)
    except _CorruptSettings as exc:
        # Keep a copy before anything can overwrite the file, and cache the
        # empty result under this signature so the backup happens once.
        _SETTINGS_CACHE.add_recovery(SettingsRecovery(path, _back_up_corrupt_file(path), str(exc)))
        payload = _default_payload()
    _SETTINGS_CACHE.put(path, signature, payload)
    return copy.deepcopy(payload)


def save_provider_settings(path: Path, payload: dict[str, Any]) -> None:
    """Write settings atomically so a crash mid-write cannot truncate the file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, ensure_ascii=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    _SETTINGS_CACHE.put(path, _SETTINGS_CACHE.signature(path), payload)


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
    has_model = bool(preferred_model_for_provider(payload, provider_name))
    
    # Local providers don't require API keys
    if is_local_capable_provider(provider_name):
        deployment_type = entry.get("deployment_type")
        if deployment_type == "local":
            # Local deployment: only need model and base_url
            has_base_url = isinstance(entry.get("base_url"), str) and entry["base_url"].strip()
            return bool(has_model and has_base_url)
        elif deployment_type == "cloud":
            # Cloud deployment: need API key and model
            has_api_key = bool(provider_api_key(payload, provider_name))
            return bool(has_api_key and has_model)
        else:
            # Not configured yet
            return False
    
    # Cloud-only providers: need API key and model
    has_api_key = bool(provider_api_key(payload, provider_name))
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
    if entry.get("catalog_fetched_at"):
        # A fetched catalog is authoritative. Do not reinsert retired defaults.
        discovered = entry.get("discovered_models", [])
        discovered = discovered if isinstance(discovered, list) else []
        return list(dict.fromkeys([name for name in [*discovered, *entry["models"]] if isinstance(name, str) and name.strip()]))
    default_model = DEFAULT_PROVIDER_MODELS.get(provider_name)
    custom_models = entry.get("models", [])
    
    # Combine default + custom, removing duplicates
    all_models = []
    if default_model:
        all_models.append(default_model)
    if provider_name == "groq":
        all_models.extend(["openai/gpt-oss-20b", "qwen/qwen3.8-27b"])
    selected_model = preferred_model_for_provider(payload, provider_name)
    if selected_model and selected_model not in all_models:
        all_models.append(selected_model)
    if provider_name == "xiaomi" and "mimo-v2.5" not in all_models:
        all_models.append("mimo-v2.5")
    
    for model in custom_models:
        if model not in all_models:
            all_models.append(model)
    
    return all_models


def set_provider_api_key(payload: dict[str, Any], provider_name: str, api_key: str) -> None:
    """Set or update API key for a provider."""
    entry = ensure_provider_entry(payload, provider_name)
    key = api_key.strip()
    if not key or any(char.isspace() or ord(char) < 32 for char in key):
        raise ValueError("Enter an API key without spaces or line breaks.")
    entry["api_key"] = key
    if not entry.get("model"):
        entry["model"] = DEFAULT_PROVIDER_MODELS.get(provider_name, "default")


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
