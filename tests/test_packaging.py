from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _project_config() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_all_scaffold_resources_are_declared_as_package_data() -> None:
    config = _project_config()
    patterns = config["tool"]["setuptools"]["package-data"]["mtp.cli"]
    assert "templates/*/.env.example.tpl" in patterns

    template_root = ROOT / "src" / "mtp" / "cli" / "templates"
    assert len(list(template_root.glob("*/.env.example.tpl"))) == 3


def test_provider_aggregate_contains_every_provider_sdk_extra() -> None:
    extras = _project_config()["project"]["optional-dependencies"]
    aggregate = set(extras["providers"])

    for name in (
        "openai", "ollama", "groq", "anthropic", "gemini", "cohere",
        "mistral", "cerebras",
    ):
        assert set(extras[name]) <= aggregate


def test_all_extra_contains_every_installable_feature() -> None:
    extras = _project_config()["project"]["optional-dependencies"]
    aggregate = set(extras["all"])

    for name in ("dotenv", "websocket", "providers", "toolkits-web", "stores-db"):
        assert set(extras[name]) <= aggregate
