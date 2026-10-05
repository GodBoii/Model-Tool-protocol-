"""Exact model documentation hints; account and endpoint support may differ."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelCapabilityHints:
    provider: str
    model: str
    image_input: bool | None = None
    file_input: bool | None = None
    json_schema: bool | None = None
    parallel_tool_calls: bool | None = None
    documentation_url: str = ""
    documentation_checked_on: str = "2026-10-05"


def model_capability_hints(provider: str, model: str) -> ModelCapabilityHints | None:
    """Return conservative hints for exact names, without model alias resolution."""
    if provider == "openai_responses" and model in {
        "gpt-4o",
        "gpt-4o-2024-08-06",
        "gpt-4o-mini",
        "gpt-4o-mini-2024-07-18",
    }:
        return ModelCapabilityHints(
            provider,
            model,
            image_input=True,
            file_input=True,
            json_schema=True,
            documentation_url="https://developers.openai.com/api/docs/guides/structured-outputs",
        )
    if provider == "groq" and model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
        return ModelCapabilityHints(
            provider,
            model,
            image_input=False,
            parallel_tool_calls=False,
            documentation_url="https://console.groq.com/docs/tool-use/overview",
        )
    return None
