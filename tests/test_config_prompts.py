from __future__ import annotations

import pytest

from mtp.config import load_dotenv_if_available, require_env
from mtp.prompts import DEFAULT_MTP_SYSTEM_INSTRUCTIONS, DEFAULT_AUTORESEARCH_SYSTEM_INSTRUCTIONS


class TestLoadDotenvIfAvailable:
    def test_returns_bool(self):
        result = load_dotenv_if_available()
        assert isinstance(result, bool)

    def test_explicit_path_missing(self, tmp_path):
        result = load_dotenv_if_available(path=str(tmp_path / "nonexistent.env"))
        assert result is True

    def test_explicit_path_exists(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_VAR=hello\n")
        result = load_dotenv_if_available(path=str(env_file))
        assert result is True


class TestRequireEnv:
    def test_existing_var(self, monkeypatch):
        monkeypatch.setenv("MTP_TEST_VAR", "hello")
        assert require_env("MTP_TEST_VAR") == "hello"

    def test_missing_var_raises(self, monkeypatch):
        monkeypatch.delenv("MTP_TEST_MISSING_VAR", raising=False)
        with pytest.raises(ValueError, match="required"):
            require_env("MTP_TEST_MISSING_VAR")

    def test_empty_var_raises(self, monkeypatch):
        monkeypatch.setenv("MTP_TEST_EMPTY", "")
        with pytest.raises(ValueError, match="required"):
            require_env("MTP_TEST_EMPTY")


class TestPrompts:
    def test_default_instructions_not_empty(self):
        assert len(DEFAULT_MTP_SYSTEM_INSTRUCTIONS) > 100

    def test_default_instructions_mention_mtp(self):
        assert "MTP" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS

    def test_default_instructions_mention_ref(self):
        assert "$ref" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS

    def test_default_instructions_mention_native_tool_calls(self):
        assert "native function/tool-calling channel" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS
        assert "tool_calls" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS

    def test_default_instructions_mention_parallel_and_sequential_batching(self):
        assert "parallel" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS.lower()
        assert "sequential" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS.lower()

    def test_default_instructions_include_native_shape_examples(self):
        assert "\"choices\"" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS
        assert "\"tool_calls\"" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS
        assert "\"arguments\"" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS

    def test_default_instructions_include_do_and_dont_rules(self):
        assert "WHAT TO DO" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS
        assert "WHAT NOT TO DO" in DEFAULT_MTP_SYSTEM_INSTRUCTIONS
        assert "Do not output tool calls as plain assistant text." in DEFAULT_MTP_SYSTEM_INSTRUCTIONS
        assert "Do not wrap a $ref object inside a string." in DEFAULT_MTP_SYSTEM_INSTRUCTIONS

    def test_autoresearch_instructions_not_empty(self):
        assert len(DEFAULT_AUTORESEARCH_SYSTEM_INSTRUCTIONS) > 100

    def test_autoresearch_instructions_mention_terminate(self):
        assert "agent.terminate" in DEFAULT_AUTORESEARCH_SYSTEM_INSTRUCTIONS
