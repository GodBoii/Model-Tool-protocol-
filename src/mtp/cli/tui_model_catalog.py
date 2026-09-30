"""Read provider model catalogs. Never send an inference request or build an agent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .tui_local_providers import discover_models
from .tui_settings import ensure_provider_entry, provider_api_key

# Metadata endpoints only. Fireworks lists its public publisher account; private
# account model IDs can always be entered manually.
MODEL_ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/models",
    "openai": "https://api.openai.com/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "claude": "https://api.anthropic.com/v1/models?limit=1000",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
    "mistral": "https://api.mistral.ai/v1/models",
    "cohere": "https://api.cohere.com/v1/models?endpoint=chat&page_size=1000",
    "deepseek": "https://api.deepseek.com/models",
    "togetherai": "https://api.together.xyz/v1/models",
    "cerebras": "https://api.cerebras.ai/v1/models",
    "sambanova": "https://api.sambanova.ai/v1/models",
    "fireworksai": "https://api.fireworks.ai/v1/accounts/fireworks/models?pageSize=1000",
    "xiaomi": "https://token-plan-ams.xiaomimimo.com/v1/models",
}


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    models: tuple[str, ...] = ()
    source: str = ""
    fetched_at: str | None = None
    error: str | None = None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward credential headers to a redirected host.
        raise HTTPError(req.full_url, code, "Model endpoint redirected", headers, fp)


def _request_json(url: str, headers: dict[str, str], timeout: float) -> Any:
    request = Request(url, headers=headers, method="GET")
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("Catalog response too large")
    return json.loads(body)


def _model_ids(provider: str, payload: Any) -> list[str]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data", payload.get("models"))
    else:
        rows = None
    if not isinstance(rows, list):
        raise ValueError("Invalid model catalog")
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("active") is False or row.get("is_deprecated") is True:
            continue
        if provider == "cohere" and "chat" not in (row.get("endpoints") or ["chat"]):
            continue
        if provider == "gemini" and "generateContent" not in (row.get("supportedGenerationMethods") or []):
            continue
        capabilities = row.get("capabilities")
        if isinstance(capabilities, dict) and capabilities.get("completion_chat") is False:
            continue
        if row.get("type") in {"embedding", "image", "audio", "rerank"}:
            continue
        model = row.get("id") or row.get("name")
        if not isinstance(model, str) or not model or len(model) > 300 or any(c.isspace() or ord(c) < 32 for c in model):
            continue
        if provider == "gemini":
            model = model.removeprefix("models/")
        # These endpoints also return specialized models the chat TUI cannot use.
        if provider in {"groq", "openai"} and any(word in model.lower() for word in (
            "whisper", "embedding", "tts", "orpheus", "transcribe", "realtime",
            "moderation", "safeguard", "prompt-guard", "dall-e", "gpt-image",
        )):
            continue
        if model not in ids:
            ids.append(model)
    return ids


def discover_provider_models(provider: str, settings: dict[str, Any], *, timeout: float = 8) -> ModelCatalog:
    """Return a complete bounded catalog, or an error that contains no credentials."""
    source = f"{provider} model API"
    if provider in {"ollama", "lmstudio"}:
        entry = ensure_provider_entry(settings, provider)
        endpoint = entry.get("base_url")
        if not isinstance(endpoint, str) or not endpoint:
            return ModelCatalog(source=source, error="Configure the local server endpoint with /apikey first.")
        result = discover_models(provider, endpoint, timeout=max(1, int(timeout)))
        if not result.success:
            return ModelCatalog(source=source, error="Could not load local models. Check the server endpoint and retry.")
        names = tuple(dict.fromkeys(model.name for model in result.models))
        return ModelCatalog(names, source, datetime.now(timezone.utc).isoformat())
    endpoint = MODEL_ENDPOINTS.get(provider)
    if endpoint is None:
        return ModelCatalog(source=source, error="No model-list endpoint is available. Enter a model ID manually.")
    key = provider_api_key(settings, provider)
    if not key and provider != "openrouter":
        return ModelCatalog(source=source, error=f"Set a {provider} key to refresh models, or enter a model ID manually.")
    headers = {"Accept": "application/json", "User-Agent": "MTP-model-catalog"}
    if provider == "claude":
        headers.update({"x-api-key": key or "", "anthropic-version": "2023-06-01"})
    elif provider == "gemini":
        headers["x-goog-api-key"] = key or ""
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    deadline = time.monotonic() + timeout
    models: list[str] = []
    url = endpoint
    cursors: set[str] = set()
    try:
        for _ in range(5):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            payload = _request_json(url, headers, remaining)
            models.extend(model for model in _model_ids(provider, payload) if model not in models)
            cursor = None
            parameter = ""
            if isinstance(payload, dict):
                if provider == "claude" and payload.get("has_more"):
                    cursor, parameter = payload.get("last_id"), "after_id"
                    if not cursor:
                        raise ValueError("Missing pagination cursor")
                elif provider == "gemini":
                    cursor, parameter = payload.get("nextPageToken"), "pageToken"
                elif provider == "cohere":
                    cursor, parameter = payload.get("next_page_token"), "page_token"
                elif provider == "fireworksai":
                    cursor, parameter = payload.get("nextPageToken"), "pageToken"
            if not cursor:
                return ModelCatalog(tuple(models), source, datetime.now(timezone.utc).isoformat())
            if not isinstance(cursor, str) or cursor in cursors:
                raise ValueError("Invalid pagination")
            cursors.add(cursor)
            url = endpoint + ("&" if "?" in endpoint else "?") + urlencode({parameter: cursor})
        return ModelCatalog(source=source, error="Catalog exceeds the page limit. Enter a model ID manually.")
    except HTTPError as exc:
        if exc.code in {401, 403, 498}:
            message = "The model API rejected access. Check the key or account permissions; cached and custom IDs remain available."
        elif exc.code == 429:
            message = "The model API is rate limited. Retry later or use a cached/custom model ID."
        else:
            message = f"Model discovery returned HTTP {exc.code}. Use a custom ID or retry later."
        return ModelCatalog(source=source, error=message)
    except (URLError, TimeoutError, OSError):
        return ModelCatalog(source=source, error="Could not reach the model API. Retry or enter a model ID manually.")
    except (ValueError, TypeError):
        return ModelCatalog(source=source, error="The model API returned an unreadable catalog. Enter a model ID manually.")


def cache_model_catalog(settings: dict[str, Any], provider: str, result: ModelCatalog) -> None:
    """Keep manually added IDs separate from an authoritative API snapshot."""
    if result.error:
        return
    entry = ensure_provider_entry(settings, provider)
    entry["discovered_models"] = list(result.models)
    entry["catalog_source"] = result.source
    entry["catalog_fetched_at"] = result.fetched_at
