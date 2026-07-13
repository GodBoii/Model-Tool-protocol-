from __future__ import annotations

import inspect
from pathlib import Path

from mtp.cli.tui_model_context import get_context_window
from mtp.cli.tui_settings import DEFAULT_PROVIDER_MODELS
from mtp.model_catalog import (
    ANTHROPIC_DEFAULT_MODEL,
    DEFAULT_MODEL_BY_PROVIDER,
    GEMINI_DEFAULT_MODEL,
    MODEL_CONTEXT_WINDOWS,
)
from mtp.providers.anthropic_provider import AnthropicToolCallingProvider
from mtp.providers.gemini_provider import GeminiToolCallingProvider


ROOT = Path(__file__).resolve().parents[1]


def _constructor_model_default(provider_type: type) -> str:
    parameter = inspect.signature(provider_type.__init__).parameters["model"]
    assert isinstance(parameter.default, str)
    return parameter.default


def test_sdk_and_tui_share_stable_cloud_model_defaults() -> None:
    assert _constructor_model_default(AnthropicToolCallingProvider) == ANTHROPIC_DEFAULT_MODEL
    assert _constructor_model_default(GeminiToolCallingProvider) == GEMINI_DEFAULT_MODEL
    assert DEFAULT_PROVIDER_MODELS["claude"] == DEFAULT_MODEL_BY_PROVIDER["anthropic"]
    assert DEFAULT_PROVIDER_MODELS["gemini"] == DEFAULT_MODEL_BY_PROVIDER["gemini"]

    for model in (ANTHROPIC_DEFAULT_MODEL, GEMINI_DEFAULT_MODEL):
        assert "preview" not in model
        assert "exp" not in model
        assert "latest" not in model


def test_tui_uses_catalog_context_windows_for_current_defaults() -> None:
    assert get_context_window("claude", ANTHROPIC_DEFAULT_MODEL) == (
        MODEL_CONTEXT_WINDOWS[ANTHROPIC_DEFAULT_MODEL],
        "model_exact",
    )
    assert get_context_window("gemini", GEMINI_DEFAULT_MODEL) == (
        MODEL_CONTEXT_WINDOWS[GEMINI_DEFAULT_MODEL],
        "model_exact",
    )
    assert get_context_window("claude", None) == (1_000_000, "provider_default")
    assert get_context_window("gemini", None) == (1_048_576, "provider_default")


def test_active_docs_and_examples_do_not_recommend_retired_defaults() -> None:
    retired_ids = (
        "claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet-latest",
        "gemini-2.0-flash",
        "gemini-2.0-flash-exp",
    )
    active_paths = [
        ROOT / "docs" / "CLI.md",
        ROOT / "docs" / "PROVIDER_GUIDES.md",
        ROOT / "docs" / "providers" / "ANTHROPIC.md",
        ROOT / "docs" / "providers" / "GEMINI.md",
        ROOT / "docs" / "providers" / "README.md",
        ROOT / "examples" / "anthropic_agent.py",
        ROOT / "examples" / "gemini_agent.py",
    ]

    for path in active_paths:
        content = path.read_text(encoding="utf-8")
        assert not any(retired_id in content for retired_id in retired_ids), path
