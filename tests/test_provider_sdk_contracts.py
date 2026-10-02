"""Real optional SDK serialization and decoding against a loopback HTTP server."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_audit_regressions import ADAPTERS

from mtp.protocol import ToolSpec
from mtp.runtime import ToolRegistry

pytestmark = pytest.mark.integration


def response_body(provider: str, tool_round: bool):
    call = {
        "id": "wire00001",
        "type": "function",
        "function": {"name": "audit_echo", "arguments": '{"value":7}'},
    }
    if provider == "anthropic":
        return {
            "id": "msg_audit",
            "type": "message",
            "role": "assistant",
            "model": "audit",
            "content": (
                [
                    {
                        "type": "tool_use",
                        "id": "wire00001",
                        "name": "audit_echo",
                        "input": {"value": 7},
                    }
                ]
                if tool_round
                else [{"type": "text", "text": "done"}]
            ),
            "stop_reason": "tool_use" if tool_round else "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    if provider == "gemini":
        part = (
            {"functionCall": {"name": "audit_echo", "args": {"value": 7}}}
            if tool_round
            else {"text": "done"}
        )
        return {
            "candidates": [
                {"content": {"role": "model", "parts": [part]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
        }
    if provider == "cohere":
        return {
            "id": "msg_audit",
            "finish_reason": "TOOL_CALL" if tool_round else "COMPLETE",
            "message": {
                "role": "assistant",
                "content": [] if tool_round else [{"type": "text", "text": "done"}],
                "tool_calls": [call] if tool_round else None,
            },
            "usage": {
                "billed_units": {"input_tokens": 10, "output_tokens": 5},
                "tokens": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    if provider == "ollama":
        return {
            "model": "audit",
            "created_at": "2026-10-03T00:00:00Z",
            "done": True,
            "message": {
                "role": "assistant",
                "content": "" if tool_round else "done",
                "tool_calls": [
                    {"function": {"name": "audit_echo", "arguments": {"value": 7}}}
                ]
                if tool_round
                else [],
            },
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
    return {
        "id": "chatcmpl-audit",
        "object": "chat.completion",
        "created": 1,
        "model": "audit",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if tool_round else "stop",
                "message": {
                    "role": "assistant",
                    "content": "" if tool_round else "done",
                    "tool_calls": [call] if tool_round else None,
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def sdk_client(provider: str, base_url: str):
    kwargs = {
        "api_key": "audit-placeholder",
        "base_url": base_url,
        "max_retries": 0,
        "timeout": 3,
    }
    if provider == "groq":
        return pytest.importorskip("groq").Groq(**kwargs)
    if provider == "anthropic":
        return pytest.importorskip("anthropic").Anthropic(**kwargs)
    if provider == "cerebras":
        return pytest.importorskip("cerebras.cloud.sdk").Cerebras(
            **kwargs, warm_tcp_connection=False
        )
    if provider == "cohere":
        return pytest.importorskip("cohere").ClientV2(**kwargs)
    if provider == "mistral":
        package = pytest.importorskip("mistralai")
        cls = getattr(package, "Mistral", None)
        if cls is None:
            cls = pytest.importorskip("mistralai.client").Mistral
        return cls(api_key="audit-placeholder", server_url=base_url, timeout_ms=3000)
    if provider == "gemini":
        package = pytest.importorskip("google.genai")
        return package.Client(
            api_key="audit-placeholder",
            http_options={
                "base_url": base_url,
                "api_version": "v1beta",
                "timeout": 3000,
            },
        )
    if provider == "ollama":
        return pytest.importorskip("ollama").Client(host=base_url, timeout=3)
    return pytest.importorskip("openai").OpenAI(**kwargs)


@pytest.mark.parametrize("provider,class_name", ADAPTERS)
def test_real_sdk_tool_round_and_result_round(provider, class_name, request):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            body = json.dumps(response_body(provider, len(requests) == 1)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = None
    try:
        client = sdk_client(provider, f"http://127.0.0.1:{server.server_address[1]}")
        if (
            provider == "anthropic"
            and "temperature"
            not in inspect.signature(client.messages.create).parameters
        ):
            request.node.add_marker(
                pytest.mark.xfail(
                    strict=True,
                    reason="A12: current Anthropic SDK rejects adapter's temperature argument",
                )
            )
        cls = getattr(
            importlib.import_module(f"mtp.providers.{provider}_provider"), class_name
        )
        adapter = cls(client=client)
        spec = ToolSpec(
            "audit_echo",
            "Return the value",
            {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )
        messages = [{"role": "user", "content": "Echo 7"}]
        action = adapter.next_action(messages, [spec])
        assert action.plan is not None
        registry = ToolRegistry()
        registry.register_tool(spec, lambda value: value)
        results = asyncio.run(registry.execute_plan(action.plan))
        assert len(results) == 1 and results[0].success and results[0].output == 7
        messages.extend(
            [
                action.metadata["assistant_tool_message"],
                {
                    "role": "tool",
                    "tool_call_id": results[0].call_id,
                    "tool_name": "audit_echo",
                    "content": "7",
                },
            ]
        )
        assert adapter.finalize(messages, results) == "done"
        assert len(requests) == 2
        assert requests[0].get("tools") or requests[0].get("config", {}).get("tools")
        assert "audit_echo" in json.dumps(requests[1])
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
