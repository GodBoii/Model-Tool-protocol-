"""Provider boundary failures must precede network I/O and tool execution."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from mtp.agent import AgentAction
from mtp.media import Image
from mtp.protocol import ToolSpec
from mtp.providers import DashScope, HuggingFace, OpenAICompatible, OpenAIResponses
from mtp.providers.compatible_provider import validate_endpoint


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        " https://example.com/v1",
        "https://example.com/v1 ",
        "https://exam\nple.com/v1",
        "https://example.com/\tsecret",
        "https://example.com/\x00",
        "https://example.com/\x7f",
        "https://example.com/\x80",
        "https://exam\u200bple.com/v1",
        "https://example.com:abc/v1",
        "https://example.com:65536/v1",
        "https://example.com:0/v1",
        "https://example.com:/v1",
        "https://[::1/v1",
        "https://@example.com/v1",
        "https://user%3Asecret@example.com/v1",
        "https://example.com%40evil.com/v1",
        "https://example.com/v1?",
        "https://example.com/v1#",
        "https://example.com\\@evil.com/v1",
    ],
)
def test_endpoint_validation_rejects_ambiguous_addresses_without_echoing_them(url):
    with pytest.raises(ValueError) as error:
        validate_endpoint(url)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:443/v1/",
        "http://127.0.0.1:8123/v1/",
        "http://[::1]:8123/v1/",
        "http://localhost/v1/",
    ],
)
def test_endpoint_validation_preserves_supported_local_and_https_routes(url):
    assert validate_endpoint(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "options,exception",
    [
        ({"stream_include_usage": "false"}, TypeError),
        ({"stream_include_usage": 1}, TypeError),
        ({"parallel_tool_calls": 0}, TypeError),
        ({"input_modalities": ()}, ValueError),
        ({"input_modalities": ("image",)}, ValueError),
        ({"input_modalities": ("text", "text")}, ValueError),
        ({"input_modalities": "text"}, TypeError),
        ({"input_modalities": ("text", [])}, ValueError),
        ({"timeout_seconds": True}, ValueError),
        ({"timeout_seconds": "60"}, ValueError),
        ({"timeout_seconds": float("inf")}, ValueError),
        ({"extra_body": [("stream", False)]}, TypeError),
    ],
)
def test_configuration_types_fail_before_client_is_used(options, exception):
    with pytest.raises(exception):
        OpenAICompatible(
            model="example", base_url="https://example.com/v1", client=NS(), **options
        )


@pytest.mark.parametrize(
    "field", ["stream_options", "temperature", "max_completion_tokens"]
)
def test_extra_body_cannot_replace_owned_stream_or_budget_settings(field):
    with pytest.raises(ValueError, match="override"):
        HuggingFace(client=NS(), extra_body={field: None})


def test_supported_configuration_builds_requests_and_copies_extra_body():
    extra = {"provider_hint": {"route": "a"}}
    provider = HuggingFace(
        client=NS(),
        stream_include_usage=False,
        parallel_tool_calls=False,
        input_modalities=("text", "image"),
        max_tokens=None,
        extra_body=extra,
    )
    extra["provider_hint"]["route"] = "mutated"
    request = provider._request(
        [{"role": "user", "content": "echo"}], [ToolSpec("echo", "echo")], stream=True
    )
    assert "stream_options" not in request and "max_tokens" not in request
    assert request["parallel_tool_calls"] is False
    assert request["extra_body"] == {"provider_hint": {"route": "a"}}
    assert provider.capabilities().input_modalities == ["text", "image"]


@pytest.mark.parametrize(
    "options",
    [
        {"max_tokens": 1024},
        {"input_modalities": ("text", "image")},
        {"input_modalities": ()},
        {"reasoning_effort": False},
        {"reasoning_effort": " "},
        *(
            {"extra_body": {field: "override"}}
            for field in (
                "conversation",
                "background",
                "store",
                "include",
                "previous_response_id",
                "reasoning",
                "max_output_tokens",
            )
        ),
    ],
)
def test_responses_unsupported_options_fail_before_key_lookup(monkeypatch, options):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        OpenAIResponses(**options)


@pytest.mark.parametrize("field", ["images", "audio", "audios", "videos", "files"])
def test_responses_rejects_media_attachments_before_inference(field):
    provider = OpenAIResponses(
        client=NS(
            responses=NS(
                create=lambda **kwargs: pytest.fail(
                    "Unsupported media reached inference."
                )
            )
        )
    )
    with pytest.raises(ValueError, match="text input only"):
        provider.next_action(
            [
                {
                    "role": "user",
                    "content": "see attachment",
                    field: [Image(url="https://example.com/image.png")],
                }
            ],
            [],
        )


@pytest.mark.parametrize(
    "kind", ["image_url", "input_image", "input_audio", "input_file", "video"]
)
def test_responses_rejects_native_media_blocks_before_inference(kind):
    provider = OpenAIResponses(
        client=NS(
            responses=NS(
                create=lambda **kwargs: pytest.fail(
                    "Unsupported media reached inference."
                )
            )
        )
    )
    with pytest.raises(ValueError, match="text input only"):
        provider.next_action(
            [
                {
                    "role": "user",
                    "content": [{"type": kind, "url": "https://example.com/media"}],
                }
            ],
            [],
        )


@pytest.mark.parametrize(
    "options",
    [
        {"region": []},
        {"workspace_id": 123},
        {"workspace_id": "-ws"},
        {"workspace_id": "ws-"},
        {"workspace_id": "w" * 64},
        {"enable_thinking": "false"},
        {"extra_body": [("enable_thinking", False)]},
    ],
)
def test_dashscope_configuration_rejects_invalid_dns_labels_and_flags(options):
    with pytest.raises((ValueError, TypeError)):
        DashScope(client=NS(), **options)


class ClosingStream:
    def __init__(self, items):
        self.items = iter(items)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.items)

    def close(self):
        self.closed = True


def tool_chunk(*, finish="tool_calls", name="echo", arguments="{}", kind="function"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call1",
                            "type": kind,
                            "function": {"name": name, "arguments": arguments},
                        }
                    ]
                },
            }
        ]
    }


@pytest.mark.parametrize(
    "chunks",
    [
        [tool_chunk(finish="error")],
        [tool_chunk(kind="custom")],
        [tool_chunk(name=23)],
        [tool_chunk(arguments='{"value":NaN}')],
        [tool_chunk(arguments='{"value":Infinity}')],
        [
            tool_chunk(),
            {
                "choices": [
                    {"finish_reason": None, "delta": {"content": "after finish"}}
                ]
            },
        ],
    ],
)
def test_invalid_native_streams_close_without_yielding_an_executable_action(chunks):
    stream = ClosingStream(chunks)
    provider = HuggingFace(
        client=NS(chat=NS(completions=NS(create=lambda **kwargs: stream)))
    )
    yielded = []
    with pytest.raises((ValueError, TypeError)):
        for item in provider.stream_next_action([], [ToolSpec("echo", "echo")]):
            yielded.append(item)  # noqa: PERF402 - retain pre-failure output
    assert not any(isinstance(item, AgentAction) for item in yielded)
    assert stream.closed


def test_stream_consumer_cancellation_closes_client_stream():
    stream = ClosingStream(
        [
            {"choices": [{"finish_reason": None, "delta": {"content": "first"}}]},
            tool_chunk(),
        ]
    )
    provider = HuggingFace(
        client=NS(chat=NS(completions=NS(create=lambda **kwargs: stream)))
    )
    iterator = provider.stream_next_action([], [])
    assert next(iterator) == {"type": "text_chunk", "chunk": "first"}
    iterator.close()
    assert stream.closed


@pytest.mark.parametrize(
    "call",
    [
        {"id": "a", "type": "custom", "function": {"name": "echo", "arguments": "{}"}},
        {"id": "a", "function": {"name": " ", "arguments": "{}"}},
        {"id": " ", "function": {"name": "echo", "arguments": "{}"}},
        {"id": "a", "function": {"name": "echo", "arguments": '{"value":NaN}'}},
    ],
)
def test_invalid_nonstream_tool_contract_is_rejected(call):
    body = {
        "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [call]}}]
    }
    provider = HuggingFace(
        client=NS(chat=NS(completions=NS(create=lambda **kwargs: body)))
    )
    with pytest.raises(ValueError):
        provider.next_action([], [ToolSpec("echo", "echo")])


def test_nonstream_unknown_finish_does_not_authorize_tool_execution():
    body = {"choices": [{"finish_reason": "error", "message": {"tool_calls": []}}]}
    provider = HuggingFace(
        client=NS(chat=NS(completions=NS(create=lambda **kwargs: body)))
    )
    with pytest.raises(ValueError, match="finish marker"):
        provider.next_action([], [])


def test_responses_keeps_json_tool_outputs_as_text_even_if_data_describes_media():
    provider = OpenAIResponses(client=NS())
    data = [{"type": "image_url", "url": "https://example.com/media"}]
    request = provider._request(
        [{"role": "tool", "tool_call_id": "c1", "content": data}], []
    )
    assert request["input"] == [
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": '[{"type": "image_url", "url": "https://example.com/media"}]',
        }
    ]


def completed_response():
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "done"}],
            }
        ],
    }


@pytest.mark.parametrize(
    "event",
    [
        {"type": "response.completed", "response": completed_response()},
        {"type": "response.output_text.delta", "delta": "late"},
        {"type": "response.function_call_arguments.delta", "delta": "late"},
    ],
)
def test_responses_rejects_output_after_completion_and_closes_stream(event):
    stream = ClosingStream(
        [{"type": "response.completed", "response": completed_response()}, event]
    )
    provider = OpenAIResponses(client=NS(responses=NS(create=lambda **kwargs: stream)))
    with pytest.raises(ValueError, match="after completion"):
        list(provider.stream_next_action([], []))
    assert stream.closed


def test_responses_preserves_native_encrypted_replay_with_text_configuration():
    body = completed_response()
    reasoning = {
        "type": "reasoning",
        "id": "r1",
        "summary": [],
        "encrypted_content": "opaque",
    }
    body["output"].insert(0, reasoning)
    provider = OpenAIResponses(
        client=NS(responses=NS(create=lambda **kwargs: body)),
        input_modalities=("text",),
    )
    action = provider.next_action([], [])
    replay = provider._request([action.metadata["assistant_message"]], [])
    assert replay["input"][0] == reasoning and replay["store"] is False
    assert replay["include"] == ["reasoning.encrypted_content"]
    assert provider.capabilities().input_modalities == ["text"]


def test_responses_rejects_unfinished_message_in_completed_envelope():
    body = completed_response()
    body["output"][0]["status"] = "in_progress"
    provider = OpenAIResponses(client=NS(responses=NS(create=lambda **kwargs: body)))
    with pytest.raises(ValueError, match="unfinished message"):
        provider.next_action([], [])
