"""Exercise CLI parsing, scaffolding and local fixture execution without AI."""
from __future__ import annotations
import pytest
from mtp.cli.main import build_parser, main
from mtp.cli.tui import _ensure_initial_provider_model
from mtp.cli.tui_settings import load_provider_settings, provider_settings_path, save_provider_settings
from test_tui_app_lifecycle import _make_state


@pytest.mark.parametrize("value", ["0", "-1", "1001", "²", "9" * 4400])
def test_cli_round_limit_rejects_bad_values(value):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["tui", "--max-rounds", value])
    assert error.value.code == 2


def test_cli_thinking_alias_and_model_override(tmp_path):
    args = build_parser().parse_args(["tui", "--thinking", "high", "--openai-model", "custom-model"])
    assert args.reasoning_effort == "high" and args.openai_model == "custom-model"
    assert build_parser().parse_args(["tui", "--reasoning-effort", "low"]).reasoning_effort == "low"
    state = _make_state(tmp_path)
    state.backend = "openai"
    path = provider_settings_path(state.session_store.file_path)
    save_provider_settings(path, {"providers": {"openai": {"model": "saved-model"}}})
    _ensure_initial_provider_model(state)
    assert load_provider_settings(path)["providers"]["openai"]["model"] == "saved-model"
    _ensure_initial_provider_model(state, model_override=args.openai_model)
    assert load_provider_settings(path)["providers"]["openai"]["model"] == "custom-model"


@pytest.mark.parametrize("template", ["minimal", "session-json", "mcp-http"])
def test_scaffold_cli_is_complete_and_does_not_overwrite(tmp_path, template):
    name = "cli-fixture"
    assert main(["new", name, "--dir", str(tmp_path), "--template", template]) == 0
    assert (tmp_path / name / "README.md").is_file()
    assert (tmp_path / name / "pyproject.toml").is_file()
    assert main(["new", name, "--dir", str(tmp_path), "--template", template]) == 1


def test_explicit_entry_with_relative_project_path(tmp_path, monkeypatch, capsys):
    project = tmp_path / "fixture"
    project.mkdir()
    (project / "local.py").write_text('print("local fixture ran")\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--path", "fixture", "--entry", "local.py"]) == 0
    assert "local fixture ran" in capsys.readouterr().out
    assert main(["run", "--path", "fixture", "--entry", "missing.py"]) == 1
    assert main(["run", "--path", str(project / "local.py")]) == 1


def test_provider_listing_and_unknown_doctor_filter(capsys):
    assert main(["providers"]) == 0
    assert "groq" in capsys.readouterr().out
    assert main(["doctor", "--provider", "does-not-exist"]) == 1


def test_doctor_recognizes_saved_key_without_revealing_it(tmp_path, monkeypatch, capsys):
    from mtp.cli.tui_settings import set_provider_api_key
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    payload = {"providers": {}}
    set_provider_api_key(payload, "groq", "test-secret-never-live")
    save_provider_settings(tmp_path / "tui_provider_settings.json", payload)
    main(["doctor", "--provider", "groq", "--session-db", str(tmp_path)])
    text = capsys.readouterr().out
    assert "Key saved locally" in text
    assert "test-secret-never-live" not in text


def test_cli_codebase_index_status_and_disable(tmp_path, capsys):
    (tmp_path / "fixture.py").write_text('VALUE = "fixture"\n', encoding="utf-8")
    assert main(["codebase", "memory", "--path", str(tmp_path), "--on"]) == 0
    assert main(["codebase", "status", "--path", str(tmp_path)]) == 0
    assert "True" in capsys.readouterr().out
    assert main(["codebase", "memory", "--path", str(tmp_path), "--off"]) == 0


def test_dotenv_templates_are_not_loaded_as_credentials(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    import os
    from mtp.config import load_dotenv_if_available
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MTP_AUDIT_ENV", raising=False)
    (tmp_path / ".env.example").write_text("MTP_AUDIT_ENV=template-placeholder\n", encoding="utf-8")
    assert not load_dotenv_if_available()
    assert "MTP_AUDIT_ENV" not in os.environ
    (tmp_path / ".env").write_text("MTP_AUDIT_ENV=fixture-value\n", encoding="utf-8")
    try:
        assert load_dotenv_if_available()
        assert os.environ["MTP_AUDIT_ENV"] == "fixture-value"
    finally:
        os.environ.pop("MTP_AUDIT_ENV", None)
