from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    "openai": "gpt-5.4-mini",
    "groq": "llama-3.3-70b-versatile",
    "claude": "claude-3-5-sonnet-20241022",
    "openrouter": "qwen/qwen3.6-plus-preview:free",
    "gemini": "gemini-2.0-flash",
}


def provider_settings_path(session_db_path: str | Path) -> Path:
    base = Path(session_db_path)
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
        entry = {"api_key": None, "model": None, "models": []}
        providers[provider_name] = entry
    if "api_key" not in entry:
        entry["api_key"] = None
    if "model" not in entry:
        entry["model"] = None
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
    return DEFAULT_PROVIDER_MODELS.get(provider_name, "gpt-5.4-mini")
