"""OpenRouter credentials, exact model IDs, budgets, and error diagnostics."""

from __future__ import annotations

import json

import pytest

from mtp.cli.tui_provider_factory import ProviderSelection, build_tui_provider
from mtp.cli.tui_settings import provider_api_key
from mtp.providers import OpenRouter


@pytest.mark.parametrize("budget", [None, 256])
def test_real_sdk_preserves_key_model_and_budget(monkeypatch, budget):
    pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment-fixture-key")
    settings = {"providers": {"openrouter": {"api_key": "saved-fixture-key"}}}
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture/model:free",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "hello",
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        # Construct the production client to verify its endpoint and credentials.
        provider = OpenRouter(
            model="fixture/model:free",
            api_key=provider_api_key(settings, "openrouter"),
            site_url="https://example.com",
            site_name="Fixture",
            max_tokens=budget,
        )
        original_client = provider._client
        provider._client = original_client.with_options(http_client=http_client)
        try:
            messages = [{"role": "user", "content": "hello"}]
            assert provider.next_action(messages, []).response_text == "hello"
            assert provider.finalize(messages, []) == "hello"
        finally:
            original_client.close()
    assert len(requests) == 2
    for request in requests:
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer saved-fixture-key"
        assert request.headers["HTTP-Referer"] == "https://example.com"
        assert request.headers["X-Title"] == "Fixture"
        payload = json.loads(request.content)
        assert payload["model"] == "fixture/model:free"
        assert "models" not in payload
        if budget is None:
            assert "max_tokens" not in payload
        else:
            assert payload["max_tokens"] == budget


@pytest.mark.parametrize("budget", [0, -1, True, "256", 1.5])
def test_invalid_budget_fails_before_key_lookup(monkeypatch, budget):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="max_tokens"):
        OpenRouter(max_tokens=budget)


def test_tui_builder_forwards_output_limit(monkeypatch):
    openai = pytest.importorskip("openai")
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment-fixture-key")
    provider = build_tui_provider(
        ProviderSelection(
            "openrouter",
            "fixture/model:free",
            "saved-fixture-key",
            provider_options={"max_tokens": 256},
        )
    )
    try:
        assert isinstance(provider._client, openai.OpenAI)
        assert provider.max_tokens == 256
        assert provider.model == "fixture/model:free"
        assert provider._client.api_key == "saved-fixture-key"
    finally:
        provider._client.close()


def test_tui_worker_loads_saved_budget_and_key(tmp_path, monkeypatch):
    from test_tui_app_lifecycle import _make_state
    from test_tui_turn_status import _EventsAgent

    from mtp.cli import tui_harness_agent, tui_provider_factory, tui_workers
    from mtp.cli.tui_settings import provider_settings_path, save_provider_settings

    state = _make_state(tmp_path)
    state.backend = "openrouter"
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment-fixture-key")
    save_provider_settings(
        provider_settings_path(state.session_store.file_path),
        {
            "providers": {
                "openrouter": {
                    "api_key": "saved-fixture-key",
                    "model": "fixture/model:free",
                    "max_tokens": 256,
                }
            },
        },
    )
    selections = []
    monkeypatch.setattr(tui_provider_factory, "build_tui_provider", selections.append)
    monkeypatch.setattr(
        tui_harness_agent, "build_harness_agent", lambda **kwargs: _EventsAgent([])
    )
    result = tui_workers._run_mtp(state, "hello")
    assert result.status == "completed"
    assert len(selections) == 1
    assert selections[0].api_key == "saved-fixture-key"
    assert selections[0].model_name == "fixture/model:free"
    assert selections[0].provider_options == {"max_tokens": 256}


@pytest.mark.parametrize(
    "status,metadata,expected",
    [
        (401, {}, "saved key takes priority"),
        (402, {"limit_source": "openrouter_credits"}, "does not add account credits"),
        (402, {"limit_source": "openrouter_key_limit"}, "spending cap is exhausted"),
        (402, {"limit_source": "openrouter_in_flight_budget"}, "Retry-After"),
        (429, {}, "rate limited"),
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_openrouter_failure_keeps_status_and_adds_recovery_hint(
    status, metadata, expected, nested
):
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    from test_tui_turn_status import _EventsAgent

    from mtp.cli.tui_mtp_backend import run_mtp_prompt

    error = {"code": status, "message": "fixture rejection", "metadata": metadata}
    exc = openai.APIStatusError(
        "fixture rejection",
        response=httpx.Response(
            status,
            request=httpx.Request(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
            ),
        ),
        body={"error": error} if nested else error,
    )
    agent = _EventsAgent([], fail_with=exc)
    result = run_mtp_prompt(
        agent=agent, prompt="hello", max_rounds=1, provider_name="openrouter"
    )
    assert result.status == "failed"
    assert "fixture rejection" in result.error
    assert expected in result.error
    # OpenRouter advice must not be attached to another provider's error.
    other = run_mtp_prompt(
        agent=agent, prompt="hello", max_rounds=1, provider_name="openai"
    )
    assert other.error == "APIStatusError: fixture rejection"
