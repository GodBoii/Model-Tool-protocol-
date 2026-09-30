"""Model discovery metadata contracts, including errors and manual overrides."""
from __future__ import annotations
import asyncio
import json
from urllib.error import HTTPError, URLError

import pytest
from textual.widgets import Input, OptionList

from mtp.cli import tui_model_catalog as catalog
from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_settings import get_provider_models, load_provider_settings, provider_settings_path, save_provider_settings, set_provider_api_key
from mtp.cli.tui_widgets.model_picker import ModelPicker
from test_tui_app_lifecycle import _make_state


@pytest.mark.parametrize("provider,payload,expected", [
    ("groq", {"data": [{"id": "openai/gpt-oss-120b", "active": True}, {"id": "retired", "active": False}, {"id": "whisper-large-v3"}]}, ["openai/gpt-oss-120b"]),
    ("openai", {"data": [{"id": "gpt-chat"}, {"id": "text-embedding-3-small"}]}, ["gpt-chat"]),
    ("gemini", {"models": [{"name": "models/gemini-current", "supportedGenerationMethods": ["generateContent"]}, {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}]}, ["gemini-current"]),
    ("claude", {"data": [{"id": "claude-current"}]}, ["claude-current"]),
    ("cohere", {"models": [{"name": "command-current", "endpoints": ["chat"]}, {"name": "command-old", "is_deprecated": True}]}, ["command-current"]),
    ("togetherai", [{"id": "org/chat", "type": "chat"}, {"id": "org/embed", "type": "embedding"}], ["org/chat"]),
    ("fireworksai", {"models": [{"name": "accounts/fireworks/models/current"}]}, ["accounts/fireworks/models/current"]),
    ("mistral", {"data": [{"id": "current", "capabilities": {"completion_chat": True}}, {"id": "embed", "capabilities": {"completion_chat": False}}]}, ["current"]),
])
def test_provider_metadata_shapes(provider, payload, expected):
    assert catalog._model_ids(provider, payload) == expected


def test_http_request_is_get_without_secret_in_url(monkeypatch):
    seen = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, limit): return b'{"data":[{"id":"fixture-model"}]}'
    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            return Response()
    monkeypatch.setattr(catalog, "build_opener", lambda *args: Opener())
    payload = {"providers": {}}
    set_provider_api_key(payload, "groq", "test-secret-never-live")
    result = catalog.discover_provider_models("groq", payload)
    assert result.models == ("fixture-model",)
    assert seen[0].get_method() == "GET" and seen[0].data is None
    assert "test-secret" not in seen[0].full_url
    assert seen[0].get_header("Authorization") == "Bearer test-secret-never-live"


def test_real_http_metadata_transport_and_redirect_refusal(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/credential-sink")
                self.end_headers()
            else:
                data = b'{"data":[{"id":"fixture/current"}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_port}"
        settings = {"providers": {"groq": {"api_key": "fixture-key"}}}
        monkeypatch.setitem(catalog.MODEL_ENDPOINTS, "groq", root + "/models")
        assert catalog.discover_provider_models("groq", settings).models == ("fixture/current",)
        monkeypatch.setitem(catalog.MODEL_ENDPOINTS, "groq", root + "/redirect")
        assert catalog.discover_provider_models("groq", settings).error
        assert requests == [("/models", "Bearer fixture-key"), ("/redirect", "Bearer fixture-key")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("code", [401, 403, 429, 404])
def test_catalog_errors_keep_cache_and_do_not_reveal_keys(monkeypatch, code):
    payload = {"providers": {"groq": {"discovered_models": ["kept"], "catalog_fetched_at": "old"}}}
    set_provider_api_key(payload, "groq", "test-secret-never-live")
    def fail(*args):
        raise HTTPError("https://api.groq.com/openai/v1/models", code, "test-secret-never-live", {}, None)
    monkeypatch.setattr(catalog, "_request_json", fail)
    result = catalog.discover_provider_models("groq", payload)
    assert result.error and "test-secret" not in result.error
    catalog.cache_model_catalog(payload, "groq", result)
    assert get_provider_models(payload, "groq") == ["kept"]


def test_paginated_catalog_is_complete(monkeypatch):
    requests = []
    def read(url, headers, timeout):
        requests.append(url)
        return {"data": [{"id": "first"}], "has_more": True, "last_id": "first"} if len(requests) == 1 else {"data": [{"id": "second"}], "has_more": False}
    monkeypatch.setattr(catalog, "_request_json", read)
    result = catalog.discover_provider_models("claude", {"providers": {"claude": {"api_key": "fixture-key"}}})
    assert result.models == ("first", "second")
    assert "after_id=first" in requests[1]


def test_live_catalog_does_not_reinsert_retired_default():
    payload = {"providers": {"groq": {"model": "llama-3.3-70b-versatile", "models": ["private-model"]}}}
    catalog.cache_model_catalog(payload, "groq", catalog.ModelCatalog(("openai/gpt-oss-120b", "openai/gpt-oss-20b"), "fixture API", "now"))
    assert get_provider_models(payload, "groq") == ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "private-model"]


def test_missing_key_does_not_contact_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    def forbidden(*args):
        pytest.fail("A missing key must not cause a network request")
    monkeypatch.setattr(catalog, "_request_json", forbidden)
    result = catalog.discover_provider_models("groq", {"providers": {}})
    assert result.error and not result.models


@pytest.mark.parametrize("payload", [None, {}, {"data": "not a list"}, {"data": [], "has_more": True}])
def test_invalid_catalog_never_replaces_cache(monkeypatch, payload):
    monkeypatch.setattr(catalog, "_request_json", lambda *args: payload)
    result = catalog.discover_provider_models("claude", {"providers": {"claude": {"api_key": "fixture-key"}}})
    assert result.error and not result.models


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (40, 15)])
def test_model_dialog_buttons_remain_visible_without_a_key(tmp_path, monkeypatch, size):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    async def scenario():
        state = _make_state(tmp_path)
        app = MTPApp(state=state)
        async with app.run_test(size=size) as pilot:
            app.push_screen(ModelPicker("groq", provider_settings_path(state.session_store.file_path)))
            await pilot.pause(.1)
            assert isinstance(app.screen, ModelPicker)
            assert "key" in str(app.screen.query_one("#model-source").render()).lower()
            actions = app.screen.query_one("#model-actions")
            assert actions.region.bottom <= size[1]
            assert actions.region.right <= size[0]
            await pilot.press("escape")
            assert not isinstance(app.screen, ModelPicker)
    asyncio.run(scenario())


def test_dismissed_model_refresh_does_not_save(tmp_path, monkeypatch):
    import threading
    from mtp.cli.tui_widgets import model_picker
    started, release = threading.Event(), threading.Event()
    def delayed(*args):
        started.set()
        release.wait(3)
        return catalog.ModelCatalog(("fixture/current",), "fixture API", "now")
    monkeypatch.setattr(model_picker, "discover_provider_models", delayed)
    async def scenario():
        state = _make_state(tmp_path)
        path = provider_settings_path(state.session_store.file_path)
        app = MTPApp(state=state)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ModelPicker("groq", path))
            await pilot.pause(.1)
            assert started.is_set()
            await pilot.press("escape")
            release.set()
            await pilot.pause(.1)
            assert not path.exists()
        release.set()
    asyncio.run(scenario())


def test_model_picker_refresh_search_custom_id_and_no_chat(tmp_path, monkeypatch):
    from mtp.cli.tui_widgets import model_picker
    monkeypatch.setattr(model_picker, "discover_provider_models", lambda *args: catalog.ModelCatalog(("fixture/current", "fixture/second"), "fixture API", "now"))
    async def scenario():
        state = _make_state(tmp_path)
        state.backend = "groq"
        path = provider_settings_path(state.session_store.file_path)
        payload = {"providers": {}}
        set_provider_api_key(payload, "groq", "fixture-key")
        save_provider_settings(path, payload)
        app = MTPApp(state=state)
        async with app.run_test(size=(80, 24)) as pilot:
            app._dispatch_command("model", "")
            await pilot.pause(.2)
            assert isinstance(app.screen, ModelPicker)
            picker = app.screen
            picker.query_one("#model-search", Input).value = "second"
            await pilot.pause(.05)
            options = picker.query_one("#model-options", OptionList)
            assert [str(o.prompt) for o in options.options] == ["fixture/second"]
            options.focus()
            await pilot.press("enter")
            await pilot.pause(.05)
            assert load_provider_settings(path)["providers"]["groq"]["model"] == "fixture/second"
            app._dispatch_command("model", "")
            await pilot.pause(.2)
            picker = app.screen
            entry = picker.query_one("#custom-model", Input)
            entry.value = "private/current-model"
            entry.focus()
            await pilot.press("enter")
            await pilot.pause(.05)
            settings = load_provider_settings(path)
            assert settings["providers"]["groq"]["model"] == "private/current-model"
            assert "private/current-model" in get_provider_models(settings, "groq")
            assert not app._model_missing_from_catalog(app.active_conversation)
            assert app.state.agent is None and not app.state.transcript
    asyncio.run(scenario())


def test_groq_thinking_options_follow_model_and_reach_builder(tmp_path, monkeypatch):
    import mtp.providers
    from mtp.cli.tui_thinking import apply_thinking_value, get_thinking_capability
    from mtp.cli.tui_provider_factory import _groq_builder
    state = _make_state(tmp_path)
    state.backend = "groq"
    path = provider_settings_path(state.session_store.file_path)
    save_provider_settings(path, {"providers": {"groq": {"model": "openai/gpt-oss-120b"}}})
    assert [option.value for option in get_thinking_capability(state).options] == ["low", "medium", "high"]
    apply_thinking_value(state, "high", persist=False)
    settings = load_provider_settings(path)
    assert settings["providers"]["groq"]["reasoning_effort"] == "high"
    with pytest.raises(ValueError):
        apply_thinking_value(state, "none", persist=False)
    # Capture constructor arguments without constructing any SDK client/agent.
    calls = []
    monkeypatch.setattr(mtp.providers, "Groq", lambda **kwargs: calls.append(kwargs))
    _groq_builder("openai/gpt-oss-120b", "fixture-key", None, {"reasoning_effort": "high"})
    assert calls[-1]["reasoning_effort"] == "high"
    _groq_builder("unknown-model", "fixture-key", None, {"reasoning_effort": "high"})
    assert calls[-1]["reasoning_effort"] is None
