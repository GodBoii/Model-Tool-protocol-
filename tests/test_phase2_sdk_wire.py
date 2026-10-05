"""Actual OpenAI SDK JSON/SSE round trips for the next provider phase."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mtp.agent import Agent
from mtp.protocol import ToolSpec
from mtp.providers import DashScope, DeepInfra, HuggingFace, OpenAIResponses
from mtp.runtime import ToolRegistry

pytestmark = pytest.mark.integration


def completion(output, *, responses, call, model):
    if responses:
        items = [
            {
                "type": "reasoning",
                "id": "rs_wire",
                "summary": [],
                "encrypted_content": "wire-encrypted",
            }
        ]
        if call:
            items += [
                {
                    "type": "function_call",
                    "id": "fc_wire",
                    "call_id": "call_wire",
                    "name": output,
                    "arguments": '{"value":7}',
                    "status": "completed",
                }
            ]
        else:
            items += [
                {
                    "type": "message",
                    "id": "msg_wire",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "result 7", "annotations": []}
                    ],
                }
            ]
        return {
            "id": "resp_wire",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": model,
            "output": items,
            "error": None,
            "incomplete_details": None,
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        }
    message = {"role": "assistant", "content": "" if call else "result 7"}
    if call:
        message["tool_calls"] = [
            {
                "id": "call_wire",
                "type": "function",
                "function": {"name": output, "arguments": '{"value":7}'},
            }
        ]
    return {
        "id": "chat_wire",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if call else "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    }


@pytest.mark.parametrize("cls", [HuggingFace, DeepInfra, DashScope, OpenAIResponses])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("max_rounds", [1, 2])
def test_real_sdk_native_round_trip(cls, stream, max_rounds):
    openai = pytest.importorskip("openai")
    requests = []
    responses = cls is OpenAIResponses

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, payload))
            history = payload.get("input", payload.get("messages", []))
            call = not any(
                i.get("role") == "tool" or i.get("type") == "function_call_output"
                for i in history
            )
            tools = payload.get("tools", [])
            wire = (
                (tools[0]["name"] if responses else tools[0]["function"]["name"])
                if tools
                else "echo"
            )
            body = completion(
                wire, responses=responses, call=call, model=payload["model"]
            )
            if payload.get("stream"):
                if responses:
                    events = (
                        []
                        if call
                        else [
                            {
                                "type": "response.output_text.delta",
                                "delta": "result 7",
                                "output_index": 0,
                                "content_index": 0,
                                "item_id": "msg_wire",
                                "sequence_number": 0,
                            }
                        ]
                    )
                    events += [
                        {
                            "type": "response.completed",
                            "response": body,
                            "sequence_number": 1,
                        }
                    ]
                else:
                    delta = (
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    **body["choices"][0]["message"]["tool_calls"][0],
                                }
                            ]
                        }
                        if call
                        else {"content": "result 7"}
                    )
                    events = [
                        {
                            "id": "chat_wire",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": payload["model"],
                            "choices": [
                                {"index": 0, "delta": delta, "finish_reason": None}
                            ],
                        },
                        {
                            "id": "chat_wire",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": payload["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "tool_calls" if call else "stop",
                                }
                            ],
                            "usage": body["usage"],
                        },
                    ]
                data = (
                    "".join("data: " + json.dumps(event) + "\n\n" for event in events)
                    + "data: [DONE]\n\n"
                ).encode()
                content_type = "text/event-stream"
            else:
                data = json.dumps(body).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    client = openai.OpenAI(
        api_key="synthetic-key", base_url=url, max_retries=0, timeout=3
    )
    try:
        provider = cls(client=client, base_url=url)
        registry = ToolRegistry()
        registry.register_tool(
            ToolSpec(
                "math.echo",
                "echo",
                {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                },
            ),
            lambda value: value,
        )
        agent = Agent(provider=provider, tools=registry)
        if stream:

            async def collect():
                return [
                    e
                    async for e in agent.arun_loop_events(
                        "echo 7", max_rounds=max_rounds, stream_tool_results=True
                    )
                ]

            events = asyncio.run(asyncio.wait_for(collect(), 5))
            assert [e["output"] for e in events if e["type"] == "tool_finished"] == [7]
            assert events[-1]["type"] == "run_completed"
            assert events[-1]["final_text"] == "result 7"
            assert any(e["type"] == "text_chunk" for e in events)
        else:
            result = agent.run_output("echo 7", max_rounds=max_rounds)
            assert (
                result.final_text == "result 7" and result.tool_results[0].output == 7
            )
        assert requests[0][0].endswith(
            "/responses" if responses else "/chat/completions"
        )
        assert len(requests) == 2
        replay = requests[1][1].get("input", requests[1][1].get("messages"))
        if responses:
            assert any(i.get("encrypted_content") == "wire-encrypted" for i in replay)
            assert any(
                i.get("type") == "function_call_output" and i["call_id"] == "call_wire"
                for i in replay
            )
        else:
            assert any(
                i.get("role") == "tool" and i["tool_call_id"] == "call_wire"
                for i in replay
            )
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
