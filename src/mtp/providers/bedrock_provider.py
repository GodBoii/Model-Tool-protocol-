"""Amazon Bedrock Converse with native tool-result and signed-reasoning replay."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import math
import os
import re
from collections.abc import Iterator
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..async_stream import async_from_sync
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    calls_to_dependency_batches,
    extract_refs,
    extract_usage_metrics,
    normalize_refs,
)
from .tool_names import ToolNameMap


def _native_json(value: Any, *, decode: bool = False) -> Any:
    if decode and isinstance(value, dict) and set(value) == {"mtp_bedrock_bytes"}:
        return base64.b64decode(value["mtp_bedrock_bytes"], validate=True)
    if isinstance(value, bytes):
        return {"mtp_bedrock_bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {key: _native_json(part, decode=decode) for key, part in value.items()}
    if isinstance(value, list):
        return [_native_json(part, decode=decode) for part in value]
    return copy.deepcopy(value)


class BedrockConverseToolCallingProvider(ProviderAdapter):
    def __init__(
        self,
        *,
        model: str,
        region: str | None = None,
        profile: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        timeout_seconds: float = 60,
        client: Any | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        additional_model_request_fields: dict[str, Any] | None = None,
    ) -> None:
        if (
            not isinstance(model, str)
            or not model
            or len(model) > 2048
            or any(char.isspace() or ord(char) < 32 for char in model)
        ):
            raise ValueError(
                "model must be an explicit Bedrock model or inference-profile ID/ARN."
            )
        self.region = (
            region
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
        )
        if not self.region or not re.fullmatch(
            r"[a-z]{2}(?:-[a-z]+)+-\d+", self.region
        ):
            raise ValueError(
                "Set region, AWS_REGION or AWS_DEFAULT_REGION for Bedrock."
            )
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive.")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
            or not 0 <= temperature <= 1
        ):
            raise ValueError("temperature must be between zero and one or None.")
        if tool_choice not in ("auto", "any") and not (
            isinstance(tool_choice, dict)
            and set(tool_choice) == {"tool"}
            and isinstance(tool_choice["tool"], dict)
            and set(tool_choice["tool"]) == {"name"}
            and isinstance(tool_choice["tool"]["name"], str)
            and tool_choice["tool"]["name"]
        ):
            raise ValueError(
                "tool_choice must be auto, any, or {'tool': {'name': registered_name}}."
            )
        if additional_model_request_fields is not None and not isinstance(
            additional_model_request_fields, dict
        ):
            raise TypeError(
                "additional_model_request_fields must be a dictionary or None."
            )
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.tool_choice = copy.deepcopy(tool_choice)
        self.additional_model_request_fields = copy.deepcopy(
            additional_model_request_fields
        )
        self._tool_names = ToolNameMap(64)
        self._last_finalize_usage = None
        self._last_finalize_message = None
        self._last_stream_usage = None
        self._owns_client = client is None
        if client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise ImportError(
                    "Install boto3 for the Bedrock Converse provider."
                ) from exc
            session = boto3.Session(profile_name=profile, region_name=self.region)
            client = session.client(
                "bedrock-runtime",
                config=Config(
                    connect_timeout=timeout_seconds,
                    read_timeout=timeout_seconds,
                    retries={"mode": "standard", "max_attempts": 2},
                ),
            )
        self._client = client

    def _request(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> dict[str, Any]:
        system = []
        conversation: list[dict[str, Any]] = []
        for message in messages:
            if any(
                message.get(field)
                for field in ("images", "audios", "audio", "videos", "files")
            ):
                raise ValueError("Bedrock Converse currently supports text input only.")
            role = message.get("role")
            content = message.get("content", "")
            if role in {"system", "developer"}:
                if not isinstance(content, str):
                    raise TypeError("Bedrock system content must be text.")
                if content:
                    system.append({"text": content})
                continue
            if role == "tool":
                call_id = message.get("tool_call_id")
                if not isinstance(call_id, str) or not call_id:
                    raise ValueError("Bedrock tool result is missing tool_call_id.")
                block = {
                    "toolResult": {
                        "toolUseId": call_id,
                        "content": [
                            {
                                "text": content
                                if isinstance(content, str)
                                else json.dumps(content, default=str)
                            }
                        ],
                    }
                }
                if isinstance(content, dict) and content.get("success") is False:
                    block["toolResult"]["status"] = "error"
                if conversation and conversation[-1]["role"] == "user":
                    conversation[-1]["content"].append(block)
                else:
                    conversation.append({"role": "user", "content": [block]})
                continue
            if role not in {"user", "assistant"}:
                raise ValueError(f"Unsupported Bedrock message role: {role!r}")
            native = message.get("bedrock_content")
            if role == "assistant" and isinstance(native, list):
                blocks = _native_json(native, decode=True)
            else:
                if not isinstance(content, str):
                    raise TypeError(
                        "Bedrock Converse currently supports text content only."
                    )
                blocks = [{"text": content}] if content else []
                for call in message.get("tool_calls") or []:
                    args = call["function"]["arguments"]
                    args = json.loads(args) if isinstance(args, str) else args
                    blocks.append(
                        {
                            "toolUse": {
                                "toolUseId": call["id"],
                                "name": self._tool_names.wire_name(
                                    call["function"]["name"]
                                ),
                                "input": args,
                            }
                        }
                    )
            if blocks:
                if conversation and conversation[-1]["role"] == role:
                    conversation[-1]["content"].extend(blocks)
                else:
                    conversation.append({"role": role, "content": blocks})
        if not conversation:
            raise ValueError(
                "Bedrock requires at least one nonempty conversation message."
            )
        request: dict[str, Any] = {
            "modelId": self.model,
            "messages": conversation,
            "inferenceConfig": {"maxTokens": self.max_tokens},
        }
        if self.temperature is not None:
            request["inferenceConfig"]["temperature"] = self.temperature
        if system:
            request["system"] = system
        if tools:
            choice = (
                {self.tool_choice: {}}
                if isinstance(self.tool_choice, str)
                else copy.deepcopy(self.tool_choice)
            )
            if "tool" in choice:
                choice["tool"]["name"] = self._tool_names.wire_name(
                    choice["tool"]["name"]
                )
            request["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": self._tool_names.wire_name(tool.name),
                            "description": tool.description,
                            "inputSchema": {
                                "json": tool.input_schema
                                or {"type": "object", "properties": {}}
                            },
                        }
                    }
                    for tool in tools
                ],
                "toolChoice": choice,
            }
        if self.additional_model_request_fields:
            request["additionalModelRequestFields"] = copy.deepcopy(
                self.additional_model_request_fields
            )
        return request

    def _action(self, response: dict[str, Any]) -> AgentAction:
        stop = response.get("stopReason")
        if stop not in {"end_turn", "stop_sequence", "tool_use"}:
            raise ValueError(
                f"Bedrock response did not complete safely: stopReason={stop!r}"
            )
        native = response.get("output", {}).get("message")
        if (
            not isinstance(native, dict)
            or native.get("role") != "assistant"
            or not isinstance(native.get("content"), list)
        ):
            raise ValueError("Bedrock returned an invalid assistant message.")
        text = []
        calls = []
        ids = set()
        index_ids: dict[int, str] = {}
        serialized_calls = []
        for block in native["content"]:
            if not isinstance(block, dict) or len(block) != 1:
                raise ValueError("Bedrock returned an invalid content block.")
            if "text" in block:
                if not isinstance(block["text"], str):
                    raise TypeError("Bedrock output text must be a string.")
                text.append(block["text"])
            elif "toolUse" in block:
                use = block["toolUse"]
                identifier = use.get("toolUseId")
                name = use.get("name")
                arguments = use.get("input")
                if (
                    not isinstance(identifier, str)
                    or not identifier
                    or identifier in ids
                ):
                    raise ValueError("Bedrock tool IDs must be nonempty and unique.")
                if (
                    not isinstance(name, str)
                    or not name
                    or not isinstance(arguments, dict)
                ):
                    raise ValueError(
                        "Bedrock tool names and argument objects are required."
                    )
                json.dumps(arguments, allow_nan=False)
                ids.add(identifier)
                index_ids[len(calls)] = identifier
                arguments = normalize_refs(arguments, index_ids, current_idx=len(calls))
                name = self._tool_names.original_name(name)
                calls.append(
                    ToolCall(
                        id=identifier,
                        name=name,
                        arguments=arguments,
                        depends_on=list(dict.fromkeys(extract_refs(arguments))),
                    )
                )
                serialized_calls.append(
                    {
                        "id": identifier,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(use["input"], allow_nan=False),
                        },
                    }
                )
            elif "reasoningContent" not in block:
                raise ValueError(
                    "Bedrock output contains unsupported non-text content."
                )
        if (stop == "tool_use") != bool(calls):
            raise ValueError(
                "Bedrock stop reason does not match its executable tool calls."
            )
        if not calls and not text:
            raise ValueError("Bedrock returned neither text nor tools.")
        content = "".join(text)
        assistant = {
            "role": "assistant",
            "content": content,
            "bedrock_content": _native_json(native["content"]),
        }
        metadata = {
            "provider": "bedrock",
            "model": self.model,
            "assistant_message": assistant,
        }
        reasoning = "".join(
            block.get("reasoningContent", {}).get("reasoningText", {}).get("text", "")
            for block in native["content"]
        )
        if reasoning:
            metadata["reasoning"] = reasoning
        raw_usage = response.get("usage", {})
        usage = extract_usage_metrics(
            {
                "usage": {
                    "input_tokens": raw_usage.get("inputTokens"),
                    "output_tokens": raw_usage.get("outputTokens"),
                    "total_tokens": raw_usage.get("totalTokens"),
                }
            }
        )
        for native_key, key in (
            ("cacheReadInputTokens", "cached_tokens"),
            ("cacheWriteInputTokens", "cache_write_tokens"),
        ):
            if type(raw_usage.get(native_key)) is int:
                usage[key] = raw_usage[native_key]
        if usage:
            metadata["usage"] = usage
        if calls:
            assistant["tool_calls"] = serialized_calls
            metadata["assistant_tool_message"] = copy.deepcopy(assistant)
            return AgentAction(
                plan=ExecutionPlan(
                    batches=calls_to_dependency_batches(calls),
                    metadata={"provider": "bedrock", "model": self.model},
                ),
                metadata=metadata,
            )
        return AgentAction(response_text=content, metadata=metadata)

    def next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        return self._action(self._client.converse(**self._request(messages, tools)))

    def stream_next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> Iterator[AgentAction | dict[str, Any]]:
        stream = self._client.converse_stream(**self._request(messages, tools))[
            "stream"
        ]
        blocks: dict[int, dict[str, Any]] = {}
        closed: set[int] = set()
        started = False
        stop = None
        usage = {}
        try:
            for event in stream:
                if not isinstance(event, dict) or len(event) != 1:
                    raise ValueError("Bedrock returned an invalid stream event.")
                kind, value = next(iter(event.items()))
                if kind.endswith("Exception"):
                    raise RuntimeError(f"Bedrock stream failed: {kind}")
                if stop is not None and kind != "metadata":
                    raise ValueError("Bedrock returned output after messageStop.")
                if kind == "messageStart":
                    if started or value.get("role") != "assistant":
                        raise ValueError("Bedrock stream has invalid messageStart.")
                    started = True
                elif kind in {
                    "contentBlockStart",
                    "contentBlockDelta",
                    "contentBlockStop",
                }:
                    index = value.get("contentBlockIndex")
                    if (
                        not started
                        or type(index) is not int
                        or index < 0
                        or index in closed
                    ):
                        raise ValueError(
                            "Bedrock stream has invalid content block sequence."
                        )
                    if kind == "contentBlockStart":
                        use = value.get("start", {}).get("toolUse")
                        if index in blocks or not isinstance(use, dict):
                            raise ValueError(
                                "Bedrock stream has invalid tool block start."
                            )
                        blocks[index] = {"toolUse": {**copy.deepcopy(use), "input": ""}}
                    elif kind == "contentBlockStop":
                        if index not in blocks:
                            raise ValueError("Bedrock stopped a missing content block.")
                        closed.add(index)
                    else:
                        delta = value.get("delta", {})
                        if "toolUse" in delta:
                            if index not in blocks or "toolUse" not in blocks[index]:
                                raise ValueError(
                                    "Bedrock tool fragment has no start record."
                                )
                            fragment = delta["toolUse"].get("input")
                            if not isinstance(fragment, str):
                                raise TypeError(
                                    "Bedrock tool argument fragments must be strings."
                                )
                            blocks[index]["toolUse"]["input"] += fragment
                        elif "text" in delta:
                            if not isinstance(delta["text"], str):
                                raise TypeError(
                                    "Bedrock text fragments must be strings."
                                )
                            block = blocks.setdefault(index, {"text": ""})
                            if "text" not in block:
                                raise ValueError(
                                    "Bedrock stream changed content block type."
                                )
                            block["text"] += delta["text"]
                            yield {"type": "text_chunk", "chunk": delta["text"]}
                        elif "reasoningContent" in delta:
                            reasoning = blocks.setdefault(
                                index, {"reasoningContent": {}}
                            )["reasoningContent"]
                            for field, fragment in delta["reasoningContent"].items():
                                if field == "redactedContent":
                                    reasoning[field] = (
                                        reasoning.get(field, b"") + fragment
                                    )
                                elif field in {"text", "signature"}:
                                    target = reasoning.setdefault("reasoningText", {})
                                    target[field] = target.get(field, "") + fragment
                                else:
                                    raise ValueError(
                                        "Unsupported Bedrock reasoning fragment."
                                    )
                            if delta["reasoningContent"].get("text"):
                                yield {
                                    "type": "reasoning_chunk",
                                    "chunk": delta["reasoningContent"]["text"],
                                }
                        else:
                            raise ValueError("Unsupported Bedrock stream delta.")
                elif kind == "messageStop":
                    if not started or set(blocks) != closed:
                        raise ValueError(
                            "Bedrock message stopped before its content blocks completed."
                        )
                    stop = value.get("stopReason")
                elif kind == "metadata":
                    if stop is None:
                        raise ValueError(
                            "Bedrock stream metadata preceded messageStop."
                        )
                    usage = value.get("usage", {})
                else:
                    raise ValueError(f"Unsupported Bedrock stream event: {kind}")
            if stop is None:
                raise ValueError("Bedrock stream ended without messageStop.")
            content = [blocks[index] for index in sorted(blocks)]
            for block in content:
                if "toolUse" in block:
                    try:
                        block["toolUse"]["input"] = json.loads(
                            block["toolUse"]["input"]
                        )
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            "Bedrock streamed tool arguments are malformed."
                        ) from exc
            yield self._action(
                {
                    "stopReason": stop,
                    "output": {"message": {"role": "assistant", "content": content}},
                    "usage": usage,
                }
            )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    async def anext_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        return await asyncio.to_thread(self.next_action, messages, tools)

    async def astream_next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ):
        async for item in async_from_sync(
            lambda: self.stream_next_action(messages, tools)
        ):
            yield item

    def finalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        action = self.next_action(messages, [])
        if action.plan:
            raise ValueError("Bedrock returned tool calls during finalization.")
        self._last_finalize_usage = action.metadata.get("usage")
        self._last_finalize_message = action.metadata["assistant_message"]
        return action.response_text or ""

    async def afinalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        return await asyncio.to_thread(self.finalize, messages, tool_results)

    def finalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ):
        for item in self.stream_next_action(messages, []):
            if isinstance(item, AgentAction):
                if item.plan:
                    raise ValueError("Bedrock returned tool calls during finalization.")
                self._last_stream_usage = item.metadata.get("usage")
                self._last_finalize_message = item.metadata["assistant_message"]
            elif item["type"] == "text_chunk":
                yield item["chunk"]

    async def afinalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ):
        async for item in async_from_sync(
            lambda: self.finalize_stream(messages, tool_results)
        ):
            yield item

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="bedrock",
            supports_parallel_tool_calls=True,
            supports_finalize_streaming=True,
            usage_metrics_quality="rich",
            supports_reasoning_metadata=True,
            structured_output_support="client_validated",
            supports_native_async=False,
            allow_finalize_stream_fallback=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await asyncio.to_thread(self._client.close)
            self._owns_client = False
