from __future__ import annotations

from pathlib import Path

import pytest

try:
    import tomllib  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    tomllib = None


PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _load_optional_dependencies() -> dict[str, list[str]]:
    if tomllib is None:
        pytest.skip("tomllib unavailable in this Python runtime")
    payload = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return payload.get("project", {}).get("optional-dependencies", {})


def test_expected_extras_exist() -> None:
    optional = _load_optional_dependencies()
    expected = {
        "dotenv",
        "websocket",
        "openai",
        "lmstudio",
        "ollama",
        "groq",
        "anthropic",
        "gemini",
        "cohere",
        "mistral",
        "openrouter",
        "sambanova",
        "cerebras",
        "deepseek",
        "togetherai",
        "fireworksai",
        "toolkits-web",
        "store-postgres",
        "store-mysql",
        "stores-db",
        "providers",
        "all",
    }
    assert expected.issubset(set(optional.keys()))


def test_all_extra_contains_provider_toolkit_store_dependencies() -> None:
    optional = _load_optional_dependencies()
    all_extra = set(optional["all"])
    for group in ("providers", "toolkits-web", "stores-db"):
        for dependency in optional[group]:
            assert dependency in all_extra
