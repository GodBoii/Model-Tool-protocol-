"""Enterprise setup keeps credentials in SDK chains and requires explicit models."""

from __future__ import annotations

import asyncio

import pytest
from test_tui_app_lifecycle import _make_state
from textual.widgets import Checkbox, Input
from tui_test_helpers import wait_until


def test_azure_factory_does_not_coerce_invalid_authentication_flags(monkeypatch):
    from mtp import providers
    from mtp.cli.tui_provider_factory import ProviderSelection, build_tui_provider

    captured = []

    def construct(**kwargs):
        captured.append(kwargs)
        if type(kwargs["use_entra"]) is not bool:
            raise TypeError("use_entra must be a boolean")

    monkeypatch.setattr(providers, "AzureOpenAI", construct)
    with pytest.raises(TypeError, match="boolean"):
        build_tui_provider(
            ProviderSelection(
                "azure_openai",
                "deployment",
                "synthetic-key",
                "https://azure.example",
                {"use_entra": "false"},
            )
        )
    assert captured[0]["use_entra"] == "false"
    assert captured[0]["api_key"] == "synthetic-key"


from mtp.cli.main import build_parser
from mtp.cli.providers import get_provider
from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_settings import (
    is_provider_configured,
    load_provider_settings,
    preferred_model_for_provider,
    provider_settings_path,
)
from mtp.cli.tui_widgets.provider_setup import ProviderSetup


@pytest.mark.parametrize("provider", ["azure_openai", "xai", "bedrock", "vertex"])
def test_registry_requires_explicit_model_and_parser_support(provider):
    assert get_provider(provider) is not None
    assert build_parser().parse_args(["tui", "--backend", provider]).backend == provider
    assert preferred_model_for_provider({"providers": {}}, provider) == ""
    assert not is_provider_configured({"providers": {}}, provider)


@pytest.mark.parametrize("provider", ["azure_openai", "xai", "bedrock", "vertex"])
@pytest.mark.parametrize("size", [(40, 15), (100, 35)])
def test_enterprise_setup_persists_platform_settings_without_credentials_in_chat(
    tmp_path, monkeypatch, provider, size
):
    for key in [
        "AZURE_OPENAI_API_KEY",
        "XAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    ]:
        monkeypatch.delenv(key, raising=False)

    async def scenario():
        state = _make_state(tmp_path)
        state.backend = provider
        app = MTPApp(state=state)
        async with app.run_test(size=size) as pilot:
            await wait_until(
                pilot,
                lambda: (
                    isinstance(app.screen, ProviderSetup)
                    and isinstance(app.screen.focused, Input)
                ),
            )
            dialog = app.screen
            dialog.query_one("#setup-model", Input).value = "explicit-deployment"
            if provider == "azure_openai":
                dialog.query_one(
                    "#setup-endpoint", Input
                ).value = "https://resource.openai.azure.com"
                dialog.query_one("#setup-entra", Checkbox).value = True
            elif provider == "xai":
                dialog.query_one("#setup-key", Input).value = "synthetic-key"
            elif provider == "bedrock":
                assert dialog.query_one("#setup-key", Input).disabled
                dialog.query_one("#setup-region", Input).value = "us-east-1"
                dialog.query_one("#setup-profile", Input).value = "certification"
            else:
                assert dialog.query_one("#setup-key", Input).disabled
                dialog.query_one("#setup-project", Input).value = "mtp-certification"
                dialog.query_one("#setup-location", Input).value = "us-central1"
            dialog._save()
            await wait_until(pilot, lambda: app.screen is not dialog)
            settings = load_provider_settings(
                provider_settings_path(state.session_store.file_path)
            )
            assert is_provider_configured(settings, provider)
            assert not state.transcript and not app._input_history
            if provider in {"azure_openai", "bedrock", "vertex"}:
                assert not settings["providers"][provider].get("api_key")

    asyncio.run(scenario())
