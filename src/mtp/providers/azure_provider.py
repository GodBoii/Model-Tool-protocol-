"""Azure OpenAI and Foundry v1 Responses, including refreshed Entra credentials."""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from .compatible_provider import validate_endpoint
from .responses_provider import OpenAIResponsesToolCallingProvider


def azure_v1_endpoint(endpoint: str) -> str:
    validated = validate_endpoint(endpoint)
    parsed = urlsplit(validated)
    if parsed.path.rstrip("/") not in {"", "/openai/v1"}:
        raise ValueError(
            "Azure endpoint must be the resource root or its /openai/v1 endpoint."
        )
    return validated if parsed.path.rstrip("/") else validated + "/openai/v1"


class AzureOpenAIResponsesToolCallingProvider(OpenAIResponsesToolCallingProvider):
    def __init__(
        self,
        *,
        model: str,
        endpoint: str | None = None,
        api_key: str | None = None,
        use_entra: bool = False,
        token_provider: Callable[[], str] | None = None,
        client: Any | None = None,
        async_client: Any | None = None,
        native_async: bool = False,
        **kwargs: Any,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be your nonempty Azure deployment name.")
        if type(use_entra) is not bool or type(native_async) is not bool:
            raise TypeError("use_entra and native_async must be booleans.")
        if token_provider is not None and not callable(token_provider):
            raise TypeError("token_provider must be a refreshable token callback.")
        if token_provider is not None and inspect.iscoroutinefunction(token_provider):
            raise TypeError(
                "token_provider must be synchronous; the adapter wraps it for native async requests."
            )
        if api_key is not None and (
            not isinstance(api_key, str) or not api_key.strip()
        ):
            raise ValueError("api_key must be a nonempty string or None.")
        if api_key and (use_entra or token_provider is not None):
            raise ValueError(
                "Choose Azure API-key authentication or Entra authentication."
            )
        if {"base_url", "provider_name", "api_version"}.intersection(kwargs):
            raise ValueError(
                "Azure v1 uses endpoint and deployment model; api_version is not required."
            )
        root = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not root:
            raise ValueError(
                "Set endpoint or AZURE_OPENAI_ENDPOINT for your Azure resource."
            )
        base_url = azure_v1_endpoint(root)
        key: Any = token_provider or api_key or os.environ.get("AZURE_OPENAI_API_KEY")
        needs_sync_client = client is None and async_client is None
        needs_async_client = native_async and async_client is None
        if (
            use_entra
            and token_provider is None
            and (needs_sync_client or needs_async_client)
        ):
            try:
                from azure.identity import (
                    DefaultAzureCredential,
                    get_bearer_token_provider,
                )
            except ImportError as exc:
                raise ImportError(
                    "Install azure-identity for Azure Entra authentication."
                ) from exc
            self._entra_credential = DefaultAzureCredential()
            key = get_bearer_token_provider(
                self._entra_credential, "https://ai.azure.com/.default"
            )
        owns_client = needs_sync_client
        owns_async_client = needs_async_client
        if needs_sync_client:
            if not key:
                raise ValueError(
                    "Set AZURE_OPENAI_API_KEY or configure refreshed Entra authentication."
                )
            from openai import OpenAI

            client = OpenAI(
                api_key=key,
                base_url=base_url,
                timeout=kwargs.get("timeout_seconds", 60),
            )
        if native_async and async_client is None:
            if not key:
                raise ValueError(
                    "Provide async_client or Azure credentials for native async execution."
                )
            from openai import AsyncOpenAI

            async_key = key
            if callable(key):

                async def async_key():
                    return await asyncio.to_thread(key)

            async_client = AsyncOpenAI(
                api_key=async_key,
                base_url=base_url,
                timeout=kwargs.get("timeout_seconds", 60),
            )
        super().__init__(
            model=model,
            api_key=key if isinstance(key, str) else None,
            base_url=base_url,
            client=client,
            async_client=async_client,
            native_async=native_async,
            provider_name="azure_openai",
            **kwargs,
        )
        self._owns_client = owns_client
        self._owns_async_client = owns_async_client

    async def aclose(self) -> None:
        try:
            await super().aclose()
        finally:
            credential = getattr(self, "_entra_credential", None)
            if credential is not None:
                await asyncio.to_thread(credential.close)
                self._entra_credential = None
