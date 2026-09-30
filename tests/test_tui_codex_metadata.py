from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from rich.console import Console

from mtp.cli import tui_codex_metadata as metadata
from mtp.cli import tui_codex_backend as backend
from mtp.cli.tui_app import MTPApp
from mtp.cli.tui_state import resolve_model
from mtp.cli.tui_thinking import apply_thinking_value, get_thinking_capability
from test_tui_app_lifecycle import _make_state


@pytest.fixture(autouse=True)
def isolate_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(metadata, "_models", ())


def test_catalog_handles_current_models_and_filters_hidden() -> None:
    models = metadata.parse_models([
        {"model": "future-model", "displayName": "Future", "isDefault": True,
         "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "ultra"}],
         "defaultReasoningEffort": "ultra"},
        {"model": "hidden-model", "hidden": True},
        {"slug": "cached", "visibility": "list", "context_window": 8000,
         "supported_reasoning_levels": [{"effort": "none"}], "default_reasoning_level": "none"},
        {"slug": "hidden-cache", "visibility": "hide"},
        {"model": "future-model"}, {"model": []}, None,
    ])
    assert [model.model for model in models] == ["future-model", "cached"]
    assert models[0].efforts == ("low", "ultra")
    assert models[0].default_effort == "ultra"
    assert models[1].context_window == 8000


def test_offline_catalog_uses_cli_cache(tmp_path, monkeypatch) -> None:
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "future-model", "supported_reasoning_levels": [{"effort": "max"}], "default_reasoning_level": "max"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(metadata, "CodexMetadataClient", lambda *args: (_ for _ in ()).throw(OSError()))
    assert metadata.refresh_codex_models("missing") == "Codex cached catalog"
    assert metadata.get_codex_model(None).model == "future-model"
    assert resolve_model("1") == "future-model"


def test_thinking_options_follow_selected_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(metadata, "_models", (
        metadata.CodexModel("future", "Future", "", ("low", "max", "ultra"), "low", True),
    ))
    state = _make_state(tmp_path)
    state.codex_model = "future"
    capability = get_thinking_capability(state)
    assert [item.value for item in capability.options] == ["low", "max", "ultra"]
    assert capability.current_value == "low"
    with pytest.raises(ValueError):
        apply_thinking_value(state, "none", persist=False)
    apply_thinking_value(state, "ultra", persist=False)
    assert state.reasoning_effort == "ultra"


@pytest.mark.parametrize("effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"])
def test_exec_passes_current_reasoning_config_key(tmp_path, effort) -> None:
    command = backend._build_codex_exec_command(
        codex_bin="codex", cwd=tmp_path, output_path=tmp_path / "reply",
        prompt="hi", model="future", reasoning_effort=effort, session_id=None,
    )
    assert f'model_reasoning_effort="{effort}"' in command
    assert not any(item.startswith('reasoning_effort=') for item in command)


def test_rate_limits_distinguish_unavailable_and_zero() -> None:
    lines = backend.format_codex_rate_limits({"rateLimitsByLimitId": {
        "main": {"primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": None},
                 "secondary": {"usedPercent": None}},
        "extra": {"primary": {"usedPercent": 100}, "credits": {"balance": "12.50"}},
    }})
    assert "75% remaining" in lines[0]
    assert "unknown remaining" in lines[1]
    assert "0% remaining" in lines[2]
    assert "extra.credits=12.50" in lines


def test_live_account_plan_and_limits_render_without_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(metadata, "read_codex_account", lambda *args: (
        {"account": {"type": "chatgpt", "email": "example@test", "planType": "plus"}},
        {"rateLimits": {"primary": {"usedPercent": 30}}}, None,
    ))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "codex-cli test\n"))
    info = backend.build_codex_account_info(codex_bin="codex", live=True)
    assert info.plan_type == "plus"
    assert info.subscription_status == "signed in"
    assert "70% remaining" in info.usage_lines[0]
    app = MTPApp(state=_make_state(tmp_path))
    console = Console(record=True, width=120)
    console.print(app._build_codex_account_view(info))
    output = console.export_text()
    assert "subscription" in output and "plus" in output and "70% remaining" in output
    app.session_saver.close()


def test_live_logout_does_not_show_cached_plan(monkeypatch) -> None:
    monkeypatch.setattr(metadata, "read_codex_account", lambda *args: ({"account": None}, {}, None))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "codex-cli test"))
    info = backend.build_codex_account_info(codex_bin="codex", live=True)
    assert info.plan_type is None and info.subscription_status == "not signed in"


def test_metadata_transport_handshake_notifications_and_timeout(tmp_path, monkeypatch) -> None:
    script = tmp_path / "server.py"
    script.write_text('''import json, sys, time
initialized = False
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialized":
        initialized = True
        continue
    if method == "wait":
        time.sleep(30)
        continue
    print(json.dumps({"method": "ignored/notification", "params": {}}), flush=True)
    if method != "initialize" and not initialized:
        result = {"error": {"code": -1, "message": "not initialized"}}
    elif method == "secret-error":
        result = {"error": {"code": -1, "message": "secret-token"}}
    else:
        result = {"result": {"ok": True}}
    print(json.dumps({"id": request["id"], **result}), flush=True)
''', encoding="utf-8")
    real_popen = subprocess.Popen
    children = []

    def start(command, **kwargs):
        child = real_popen([sys.executable, "-u", str(script)], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", start)
    with metadata.CodexMetadataClient("codex", timeout=1) as client:
        assert client.request("model/list") == {"ok": True}
        with pytest.raises(metadata.CodexMetadataError) as error:
            client.request("secret-error")
        assert "secret-token" not in str(error.value)
        client.timeout = 0.05
        with pytest.raises(metadata.CodexMetadataError, match="timed out"):
            client.request("wait")
    assert all(child.poll() is not None for child in children)


def test_codex_streams_deltas_and_completed_message_once() -> None:
    events = []
    emitted = {}
    for event in [
        {"type": "response.output_text.delta", "delta": "hello"},
        {"type": "response.output_text.delta", "delta": " hello"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "hello hello"}},
    ]:
        backend.emit_codex_live_line(json.dumps(event), emitted, lambda kind, text: events.append((kind, text)))
    assert "".join(text for kind, text in events if kind == "text") == "hello hello"
