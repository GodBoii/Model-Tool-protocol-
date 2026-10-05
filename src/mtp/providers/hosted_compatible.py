"""Hosted endpoints sharing the validated Chat Completions implementation."""

from __future__ import annotations

import re
from typing import Any

from ..config import require_env
from .compatible_provider import OpenAICompatibleToolCallingProvider
from .defaults import DEFAULT_PROVIDER_MODELS

DASHSCOPE_ENDPOINTS = {
    "singapore": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "beijing": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "virginia": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
}
DASHSCOPE_WORKSPACE_REGIONS = {
    "singapore": "ap-southeast-1",
    "beijing": "cn-beijing",
    "hongkong": "cn-hongkong",
    "tokyo": "ap-northeast-1",
}


class HuggingFaceToolCallingProvider(OpenAICompatibleToolCallingProvider):
    def __init__(
        self,
        *,
        model: str = DEFAULT_PROVIDER_MODELS["huggingface"],
        api_key: str | None = None,
        base_url: str = "https://router.huggingface.co/v1",
        **kwargs: Any,
    ) -> None:
        key = api_key or (
            require_env("HF_TOKEN") if kwargs.get("client") is None else None
        )
        super().__init__(
            model=model,
            api_key=key,
            base_url=base_url,
            provider_name="huggingface",
            **kwargs,
        )


class DeepInfraToolCallingProvider(OpenAICompatibleToolCallingProvider):
    def __init__(
        self,
        *,
        model: str = DEFAULT_PROVIDER_MODELS["deepinfra"],
        api_key: str | None = None,
        base_url: str = "https://api.deepinfra.com/v1/openai",
        **kwargs: Any,
    ) -> None:
        key = api_key or (
            require_env("DEEPINFRA_API_KEY") if kwargs.get("client") is None else None
        )
        super().__init__(
            model=model,
            api_key=key,
            base_url=base_url,
            provider_name="deepinfra",
            **kwargs,
        )


class DashScopeToolCallingProvider(OpenAICompatibleToolCallingProvider):
    def __init__(
        self,
        *,
        model: str = DEFAULT_PROVIDER_MODELS["dashscope"],
        api_key: str | None = None,
        region: str = "singapore",
        workspace_id: str | None = None,
        base_url: str | None = None,
        enable_thinking: bool = False,
        extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if region not in {*DASHSCOPE_ENDPOINTS, *DASHSCOPE_WORKSPACE_REGIONS}:
            raise ValueError("Unsupported DashScope region.")
        if workspace_id is not None:
            if (
                not re.fullmatch(r"[a-zA-Z0-9-]+", workspace_id)
                or region not in DASHSCOPE_WORKSPACE_REGIONS
            ):
                raise ValueError(
                    "Workspace ID or region is invalid for workspace endpoints."
                )
            if base_url is not None:
                raise ValueError("Choose workspace_id or base_url, not both.")
            base_url = f"https://{workspace_id}.{DASHSCOPE_WORKSPACE_REGIONS[region]}.maas.aliyuncs.com/compatible-mode/v1"
        if base_url is None and region not in DASHSCOPE_ENDPOINTS:
            raise ValueError("This region requires workspace_id or base_url.")
        if type(enable_thinking) is not bool:
            raise TypeError("enable_thinking must be a boolean.")
        self.enable_thinking = enable_thinking
        body = dict(extra_body or {})
        if "enable_thinking" in body:
            raise ValueError("Use the enable_thinking constructor option.")
        body["enable_thinking"] = enable_thinking
        key = api_key or (
            require_env("DASHSCOPE_API_KEY") if kwargs.get("client") is None else None
        )
        kwargs.setdefault("parallel_tool_calls", True)
        super().__init__(
            model=model,
            api_key=key,
            base_url=base_url or DASHSCOPE_ENDPOINTS[region],
            provider_name="dashscope",
            extra_body=body,
            **kwargs,
        )

    def _request(self, messages, tools, *, stream=False):
        if self.enable_thinking and not stream:
            raise ValueError("DashScope thinking requires a streaming agent API.")
        return super()._request(messages, tools, stream=stream)
