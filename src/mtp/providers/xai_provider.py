"""xAI Responses using local replay of native reasoning and tool items."""

from __future__ import annotations

import os
from typing import Any

from .responses_provider import OpenAIResponsesToolCallingProvider


class XAIResponsesToolCallingProvider(OpenAIResponsesToolCallingProvider):
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str = "https://api.x.ai/v1",
        **kwargs: Any,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be an explicit nonempty xAI model ID.")
        if "provider_name" in kwargs:
            raise ValueError("XAIResponses provider_name is fixed to xai.")
        if api_key is not None and (
            not isinstance(api_key, str) or not api_key.strip()
        ):
            raise ValueError("api_key must be a nonempty string or None.")
        key = api_key or os.environ.get("XAI_API_KEY")
        if (
            key is None
            and kwargs.get("client") is None
            and kwargs.get("async_client") is None
        ):
            raise ValueError("Set XAI_API_KEY or pass an explicit client.")
        super().__init__(
            model=model, api_key=key, base_url=base_url, provider_name="xai", **kwargs
        )
