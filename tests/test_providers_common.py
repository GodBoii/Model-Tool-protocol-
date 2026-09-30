from __future__ import annotations

import pytest
from mtp.providers.common import (
    ProviderCapabilities,
    capabilities_from_any,
    extract_usage_metrics,
    extract_refs,
    normalize_refs,
    safe_load_arguments,
    calls_to_dependency_batches,
    format_openai_like_message,
    _normalize_ref_value,
)
from mtp.protocol import ToolCall, ToolBatch
from mtp.media import Image, Audio, File


class TestProviderCapabilities:
    def test_defaults(self):
        caps = ProviderCapabilities(provider="test")
        assert caps.supports_tool_calling is True
        assert caps.supports_parallel_tool_calls is False
        assert "text" in caps.input_modalities

    def test_supports_input_modality(self):
        caps = ProviderCapabilities(provider="test", input_modalities=["text", "image"])
        assert caps.supports_input_modality("text") is True
        assert caps.supports_input_modality("image") is True
        assert caps.supports_input_modality("audio") is False

    def test_to_dict(self):
        caps = ProviderCapabilities(provider="test")
        d = caps.to_dict()
        assert d["provider"] == "test"
        assert "supports_tool_calling" in d


class TestCapabilitiesFromAny:
    def test_none(self):
        assert capabilities_from_any(None) is None

    def test_already_capabilities(self):
        caps = ProviderCapabilities(provider="test")
        assert capabilities_from_any(caps) is caps

    def test_from_dict(self):
        d = {"provider": "test", "supports_tool_calling": True}
        caps = capabilities_from_any(d)
        assert caps is not None
        assert caps.provider == "test"

    def test_from_dict_minimal(self):
        caps = capabilities_from_any({})
        assert caps is not None
        assert caps.provider == "unknown"

    def test_non_dict_returns_none(self):
        assert capabilities_from_any("not a dict") is None
        assert capabilities_from_any(42) is None


class TestExtractUsageMetrics:
    def test_empty(self):
        assert extract_usage_metrics({}) == {}

    def test_openai_format(self):
        class Usage:
            prompt_tokens = 100
            completion_tokens = 50
            total_tokens = 150
        class Response:
            usage = Usage()
        m = extract_usage_metrics(Response())
        assert m["input_tokens"] == 100
        assert m["output_tokens"] == 50
        assert m["total_tokens"] == 150

    def test_dict_format(self):
        response = {"usage": {"input_tokens": 10, "output_tokens": 5}}
        m = extract_usage_metrics(response)
        assert m["input_tokens"] == 10
        assert m["output_tokens"] == 5


class TestExtractRefs:
    def test_simple(self):
        assert extract_refs({"$ref": "c1"}) == ["c1"]

    def test_nested(self):
        refs = extract_refs({"a": {"$ref": "c1"}, "b": {"$ref": "c2"}})
        assert set(refs) == {"c1", "c2"}

    def test_none(self):
        assert extract_refs({}) == []


class TestNormalizeRefs:
    def test_string_ref_to_known_id(self):
        id_map = {0: "call_1", 1: "call_2"}
        result = normalize_refs({"$ref": "call_1"}, id_map)
        assert result["$ref"] == "call_1"

    def test_int_ref(self):
        id_map = {0: "call_1", 1: "call_2"}
        result = normalize_refs({"$ref": 0}, id_map)
        assert result["$ref"] == "call_1"

    def test_digit_string_ref(self):
        id_map = {0: "call_1", 1: "call_2"}
        result = normalize_refs({"$ref": "0"}, id_map)
        assert result["$ref"] == "call_1"

    def test_call_prefix_ref(self):
        id_map = {0: "call_1"}
        result = normalize_refs({"$ref": "call_1"}, id_map)
        assert result["$ref"] == "call_1"

    def test_last_ref(self):
        id_map = {0: "call_1", 1: "call_2"}
        result = normalize_refs({"$ref": "last"}, id_map, current_idx=2)
        assert result["$ref"] == "call_2"

    def test_c_prefix_ref(self):
        id_map = {0: "call_1", 1: "call_2"}
        result = normalize_refs({"$ref": "c1"}, id_map)
        assert result["$ref"] == "call_1"

    def test_preserves_non_ref(self):
        result = normalize_refs({"key": "value"}, {})
        assert result == {"key": "value"}


class TestSafeLoadArguments:
    def test_dict(self):
        assert safe_load_arguments({"a": 1}) == {"a": 1}

    def test_json_string(self):
        assert safe_load_arguments('{"a": 1}') == {"a": 1}

    def test_empty_string(self):
        assert safe_load_arguments("") == {}

    def test_none(self):
        assert safe_load_arguments(None) == {}

    def test_invalid_json(self):
        result = safe_load_arguments("not json")
        assert "_raw_arguments" in result

    def test_non_dict_json(self):
        result = safe_load_arguments("[1, 2]")
        assert "_raw_arguments" in result


class TestCallsToDependencyBatches:
    def test_independent_calls(self):
        calls = [ToolCall(id="c1", name="a"), ToolCall(id="c2", name="b")]
        batches = calls_to_dependency_batches(calls)
        assert len(batches) == 1
        assert batches[0].mode == "parallel"
        assert len(batches[0].calls) == 2

    def test_dependent_calls(self):
        calls = [
            ToolCall(id="c1", name="a"),
            ToolCall(id="c2", name="b", depends_on=["c1"]),
        ]
        batches = calls_to_dependency_batches(calls)
        assert len(batches) == 2
        assert batches[0].calls[0].id == "c1"
        assert batches[1].calls[0].id == "c2"

    def test_empty(self):
        batches = calls_to_dependency_batches([])
        assert batches == []

    def test_chain(self):
        calls = [
            ToolCall(id="c1", name="a"),
            ToolCall(id="c2", name="b", depends_on=["c1"]),
            ToolCall(id="c3", name="c", depends_on=["c2"]),
        ]
        batches = calls_to_dependency_batches(calls)
        assert len(batches) == 3


class TestFormatOpenAILikeMessage:
    def test_system_message(self):
        msg = {"role": "system", "content": "You are helpful."}
        result = format_openai_like_message(msg)
        assert result["role"] == "system"
        assert result["content"] == "You are helpful."

    def test_user_message(self):
        msg = {"role": "user", "content": "Hello"}
        result = format_openai_like_message(msg)
        assert result["role"] == "user"

    def test_tool_message(self):
        msg = {"role": "tool", "tool_call_id": "tc1", "content": "result"}
        result = format_openai_like_message(msg)
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "tc1"

    def test_unknown_role_returns_none(self):
        msg = {"role": "unknown", "content": "x"}
        assert format_openai_like_message(msg) is None

    def test_with_image(self):
        img = Image(url="http://x.com/img.png")
        msg = {"role": "user", "content": "Look", "images": [img]}
        result = format_openai_like_message(msg)
        assert isinstance(result["content"], list)
        assert any(p.get("type") == "image_url" for p in result["content"])

    def test_with_audio(self):
        audio = Audio(content=b"RIFF", format="wav")
        msg = {"role": "user", "content": "Listen", "audios": [audio]}
        result = format_openai_like_message(msg)
        assert isinstance(result["content"], list)
