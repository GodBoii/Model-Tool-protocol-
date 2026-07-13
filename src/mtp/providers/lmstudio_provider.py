from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import (
    ProviderCapabilities,
    STRUCTURED_OUTPUT_CLIENT_VALIDATED,
    USAGE_METRICS_RICH,
    calls_to_dependency_batches,
    extract_refs,
    extract_usage_metrics,
    format_openai_like_message,
    normalize_refs,
    safe_load_arguments,
)
from ._config import optional_positive_int, positive_timeout_seconds


class LMStudioToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for LM Studio's local OpenAI-compatible server.

    By default LM Studio serves an OpenAI-compatible API at:
    http://127.0.0.1:1234/v1

    Authentication is usually not required for local use. The OpenAI client
    still expects an api_key field, so this adapter supplies a harmless default
    token when one is not provided.
    """

    def __init__(
        self,
        *,
        model: str = "qwen3",
        base_url: str = "http://127.0.0.1:1234/v1",
        api_key: str | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict[str, Any] = "auto",
        parallel_tool_calls: bool = True,
        max_tokens: int | None = None,
        timeout_seconds: float = 300.0,
        client: Any | None = None,
        async_client: Any | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.tool_choice = tool_choice
        self.parallel_tool_calls = parallel_tool_calls
        self.max_tokens = optional_positive_int(max_tokens, field="max_tokens")
        self.timeout_seconds = positive_timeout_seconds(timeout_seconds)
        self._last_finalize_usage: dict[str, int] | None = None
        self._last_stream_usage: dict[str, int] | None = None
        self._api_key = api_key or os.getenv("LMSTUDIO_API_KEY") or "lm-studio"
        self._client = client or self._make_client(api_key=api_key)
        self._async_client = async_client

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. LM Studio uses the OpenAI-compatible API. "
                "Install with: pip install openai"
            ) from exc

        return OpenAI(
            base_url=self.base_url,
            api_key=self._api_key,
            timeout=self.timeout_seconds,
        )

    def _get_async_client(self) -> Any:
        """Return a lazily-created native OpenAI-compatible async client."""
        if self._async_client is not None:
            return self._async_client
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "`openai` not installed. LM Studio uses the OpenAI-compatible API. "
                "Install with: pip install openai"
            ) from exc
        self._async_client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self._api_key,
            timeout=self.timeout_seconds,
        )
        return self._async_client

    def _generation_args(self) -> dict[str, Any]:
        if self.max_tokens is None:
            return {}
        return {"max_tokens": self.max_tokens}

    async def _acreate_completion(self, request_args: dict[str, Any]) -> Any:
        """Create a completion, tolerating older LM Studio tool APIs."""
        try:
            return await self._get_async_client().chat.completions.create(
                **request_args
            )
        except TypeError:
            compatible_args = dict(request_args)
            if "parallel_tool_calls" not in compatible_args:
                raise
            compatible_args.pop("parallel_tool_calls")
            return await self._get_async_client().chat.completions.create(
                **compatible_args
            )

    def _to_lmstudio_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            converted = format_openai_like_message(
                msg,
                allow_images=True,
                allow_audio=False,
                allow_video=False,
                allow_files=False,
            )
            if converted is not None:
                formatted.append(converted)
        return formatted

    def _to_lmstudio_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
        ]

    def _action_from_response(self, response: Any) -> AgentAction:
        message = response.choices[0].message
        reasoning = (
            getattr(message, "reasoning_content", None)
            or getattr(message, "reasoning", None)
            or ""
        )
        action_meta: dict[str, Any] = {"provider": "lmstudio", "model": self.model}
        usage = extract_usage_metrics(response)
        if usage:
            action_meta["usage"] = usage
        if reasoning.strip():
            action_meta["reasoning"] = reasoning.strip()
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            return AgentAction(response_text=message.content or "", metadata=action_meta)
        return self._tool_action(
            content=message.content or "",
            reasoning=reasoning,
            action_meta=action_meta,
            tool_calls=[
                {
                    "id": getattr(tc, "id", None),
                    "function": {
                        "name": getattr(tc.function, "name", ""),
                        "arguments": getattr(tc.function, "arguments", "") or "",
                    },
                }
                for tc in tool_calls
            ],
        )

    @staticmethod
    def _merge_stream_tool_calls(
        accumulator: dict[int, dict[str, Any]], fragments: Any
    ) -> None:
        if not fragments:
            return
        for fragment in fragments:
            index = getattr(fragment, "index", 0)
            entry = accumulator.setdefault(
                index, {"id": None, "function": {"name": "", "arguments": ""}}
            )
            fragment_id = getattr(fragment, "id", None)
            if fragment_id:
                entry["id"] = fragment_id
            function = getattr(fragment, "function", None)
            if function is None:
                continue
            name = getattr(function, "name", None)
            arguments = getattr(function, "arguments", None)
            if name:
                entry["function"]["name"] += name
            if arguments:
                entry["function"]["arguments"] += arguments

    def _action_from_stream_parts(
        self,
        *,
        content: str,
        reasoning: str,
        usage: dict[str, int] | None,
        tool_calls_dict: dict[int, dict[str, Any]],
    ) -> AgentAction:
        meta: dict[str, Any] = {"provider": "lmstudio", "model": self.model}
        if usage:
            meta["usage"] = usage
        if reasoning.strip():
            meta["reasoning"] = reasoning.strip()
        if not tool_calls_dict:
            return AgentAction(response_text=content, metadata=meta)
        ordered = [tool_calls_dict[index] for index in sorted(tool_calls_dict)]
        return self._tool_action(
            content=content,
            reasoning=reasoning,
            action_meta=meta,
            tool_calls=ordered,
        )

    def _tool_action(
        self,
        *,
        content: str,
        reasoning: str,
        action_meta: dict[str, Any],
        tool_calls: list[dict[str, Any]],
    ) -> AgentAction:
        mtp_calls: list[ToolCall] = []
        serialized: list[dict[str, Any]] = []
        id_by_index: dict[int, str] = {}
        call_reasoning = reasoning.strip() or None
        for idx, tc in enumerate(tool_calls):
            call_id = tc.get("id") or f"call_{idx}"
            id_by_index[idx] = call_id
            function = tc.get("function") or {}
            arguments = function.get("arguments") or ""
            parsed_args = safe_load_arguments(arguments)
            normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=idx)
            tool_name = function.get("name") or ""
            mtp_calls.append(
                ToolCall(
                    id=call_id,
                    name=tool_name,
                    arguments=normalized_args,
                    depends_on=list(dict.fromkeys(extract_refs(normalized_args))),
                    reasoning=call_reasoning,
                )
            )
            serialized.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": arguments or "{}"},
                    "reasoning": call_reasoning,
                }
            )
        return AgentAction(
            plan=ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "lmstudio", "model": self.model},
            ),
            metadata={
                **action_meta,
                "assistant_tool_message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": serialized,
                    "reasoning": reasoning,
                },
            },
        )

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        lmstudio_messages = self._to_lmstudio_messages(messages)
        lmstudio_tools = self._to_lmstudio_tools(tools)

        request_args: dict[str, Any] = {
            **self._generation_args(),
            "model": self.model,
            "messages": lmstudio_messages,
            "temperature": self.temperature,
        }
        if lmstudio_tools:
            request_args["tools"] = lmstudio_tools
            request_args["tool_choice"] = self.tool_choice
            request_args["parallel_tool_calls"] = self.parallel_tool_calls

        try:
            response = self._client.chat.completions.create(**request_args)
        except TypeError:
            request_args.pop("parallel_tool_calls", None)
            response = self._client.chat.completions.create(**request_args)

        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)
        usage = extract_usage_metrics(response)
        action_meta: dict[str, Any] = {"provider": "lmstudio", "model": self.model}
        if usage:
            action_meta["usage"] = usage

        if tool_calls:
            mtp_calls: list[ToolCall] = []
            id_by_index: dict[int, str] = {}
            serialized_tool_calls: list[dict[str, Any]] = []
            call_reasoning: str | None = None
            for idx, tc in enumerate(tool_calls):
                call_id = tc.id or f"call_{idx}"
                id_by_index[idx] = call_id
                parsed_args = safe_load_arguments(tc.function.arguments)
                normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=idx)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                mtp_calls.append(
                    ToolCall(
                        id=call_id,
                        name=tc.function.name,
                        arguments=normalized_args,
                        depends_on=depends_on,
                        reasoning=call_reasoning,
                    )
                )
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                        "reasoning": call_reasoning,
                    }
                )

            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "lmstudio", "model": self.model},
            )
            return AgentAction(
                plan=plan,
                metadata={
                    **action_meta,
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": serialized_tool_calls,
                    },
                },
            )

        return AgentAction(response_text=message.content or "", metadata=action_meta)

    def stream_next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> Iterator[AgentAction | dict[str, Any]]:
        lmstudio_messages = self._to_lmstudio_messages(messages)
        lmstudio_tools = self._to_lmstudio_tools(tools)

        request_args: dict[str, Any] = {
            **self._generation_args(),
            "model": self.model,
            "messages": lmstudio_messages,
            "temperature": self.temperature,
            "stream": True,
        }
        if lmstudio_tools:
            request_args["tools"] = lmstudio_tools
            request_args["tool_choice"] = self.tool_choice
            request_args["parallel_tool_calls"] = self.parallel_tool_calls

        try:
            stream = self._client.chat.completions.create(**request_args)
        except TypeError:
            request_args.pop("parallel_tool_calls", None)
            stream = self._client.chat.completions.create(**request_args)

        content_acc = ""
        reasoning_acc = ""
        usage = None
        tool_calls_dict: dict[int, Any] = {}

        for chunk in stream:
            chunk_usage = extract_usage_metrics(chunk)
            if chunk_usage:
                usage = chunk_usage

            if not getattr(chunk, "choices", None):
                continue

            delta = chunk.choices[0].delta
            
            chunk_reasoning = getattr(delta, "reasoning_content", None)
            chunk_content = getattr(delta, "content", None)

            if chunk_reasoning and isinstance(chunk_reasoning, str):
                yield {"type": "reasoning_chunk", "chunk": chunk_reasoning}
                reasoning_acc += chunk_reasoning

            if chunk_content and isinstance(chunk_content, str):
                yield {"type": "text_chunk", "chunk": chunk_content}
                content_acc += chunk_content

            chunk_tool_calls = getattr(delta, "tool_calls", None)
            if chunk_tool_calls:
                for tc in chunk_tool_calls:
                    index = tc.index
                    if index not in tool_calls_dict:
                        tool_calls_dict[index] = {
                            "id": tc.id, 
                            "type": "function", 
                            "function": {"name": getattr(tc.function, "name", ""), "arguments": getattr(tc.function, "arguments", "") or ""}
                        }
                    else:
                        if getattr(tc.function, "arguments", None):
                            tool_calls_dict[index]["function"]["arguments"] += tc.function.arguments

        action_meta: dict[str, Any] = {"provider": "lmstudio", "model": self.model}
        if usage:
            action_meta["usage"] = usage
        if reasoning_acc.strip():
            action_meta["reasoning"] = reasoning_acc.strip()

        if tool_calls_dict:
            mtp_calls: list[ToolCall] = []
            id_by_index: dict[int, str] = {}
            serialized_tool_calls: list[dict[str, Any]] = []
            call_reasoning = reasoning_acc.strip() if reasoning_acc.strip() else None
            
            for idx, tc in tool_calls_dict.items():
                call_id = tc["id"] or f"call_{idx}"
                id_by_index[idx] = call_id
                args_str = tc["function"]["arguments"]
                parsed_args = safe_load_arguments(args_str)
                normalized_args = normalize_refs(parsed_args, id_by_index, current_idx=idx)
                depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                tool_name = tc["function"]["name"]
                
                mtp_calls.append(
                    ToolCall(
                        id=call_id,
                        name=tool_name,
                        arguments=normalized_args,
                        depends_on=depends_on,
                        reasoning=call_reasoning,
                    )
                )
                serialized_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": args_str},
                        "reasoning": call_reasoning,
                    }
                )

            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(mtp_calls),
                metadata={"provider": "lmstudio", "model": self.model},
            )
            yield AgentAction(
                plan=plan,
                metadata={
                    **action_meta,
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": content_acc,
                        "tool_calls": serialized_tool_calls,
                        "reasoning": reasoning_acc,
                    },
                },
            )
            return

        yield AgentAction(response_text=content_acc, metadata=action_meta)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        lmstudio_messages = self._to_lmstudio_messages(messages)
        response = self._client.chat.completions.create(
            **self._generation_args(),
            model=self.model,
            messages=lmstudio_messages,
            temperature=self.temperature,
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    def finalize_stream(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> Iterator[str]:
        lmstudio_messages = self._to_lmstudio_messages(messages)
        self._last_stream_usage = None
        stream = self._client.chat.completions.create(
            **self._generation_args(),
            model=self.model,
            messages=lmstudio_messages,
            temperature=self.temperature,
            stream=True,
        )
        for chunk in stream:
            chunk_usage = extract_usage_metrics(chunk)
            if chunk_usage:
                self._last_stream_usage = chunk_usage
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield content

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="lmstudio",
            supports_tool_calling=True,
            supports_parallel_tool_calls=bool(self.parallel_tool_calls),
            input_modalities=["text", "image"],
            supports_tool_media_output=True,
            supports_finalize_streaming=True,
            usage_metrics_quality=USAGE_METRICS_RICH,
            supports_reasoning_metadata=True,
            structured_output_support=STRUCTURED_OUTPUT_CLIENT_VALIDATED,
            supports_native_async=True,
            allow_finalize_stream_fallback=True,
        )

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        lmstudio_tools = self._to_lmstudio_tools(tools)
        request_args: dict[str, Any] = {
            **self._generation_args(),
            "model": self.model,
            "messages": self._to_lmstudio_messages(messages),
            "temperature": self.temperature,
        }
        if lmstudio_tools:
            request_args.update(
                tools=lmstudio_tools,
                tool_choice=self.tool_choice,
                parallel_tool_calls=self.parallel_tool_calls,
            )
        response = await self._acreate_completion(request_args)
        return self._action_from_response(response)

    async def astream_next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AsyncIterator[AgentAction | dict[str, Any]]:
        lmstudio_tools = self._to_lmstudio_tools(tools)
        request_args: dict[str, Any] = {
            **self._generation_args(),
            "model": self.model,
            "messages": self._to_lmstudio_messages(messages),
            "temperature": self.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if lmstudio_tools:
            request_args.update(
                tools=lmstudio_tools,
                tool_choice=self.tool_choice,
                parallel_tool_calls=self.parallel_tool_calls,
            )
        stream = await self._acreate_completion(request_args)
        content_acc = ""
        reasoning_acc = ""
        usage: dict[str, int] | None = None
        tool_calls_dict: dict[int, dict[str, Any]] = {}
        async for chunk in stream:
            chunk_usage = extract_usage_metrics(chunk)
            if chunk_usage:
                usage = chunk_usage
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            chunk_reasoning = (
                getattr(delta, "reasoning_content", None)
                or getattr(delta, "reasoning", None)
            )
            chunk_content = getattr(delta, "content", None)
            if isinstance(chunk_reasoning, str) and chunk_reasoning:
                reasoning_acc += chunk_reasoning
                yield {"type": "reasoning_chunk", "chunk": chunk_reasoning}
            if isinstance(chunk_content, str) and chunk_content:
                content_acc += chunk_content
                yield {"type": "text_chunk", "chunk": chunk_content}
            self._merge_stream_tool_calls(
                tool_calls_dict, getattr(delta, "tool_calls", None)
            )
        yield self._action_from_stream_parts(
            content=content_acc,
            reasoning=reasoning_acc,
            usage=usage,
            tool_calls_dict=tool_calls_dict,
        )

    async def afinalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        del tool_results
        response = await self._acreate_completion(
            {
                "model": self.model,
                "messages": self._to_lmstudio_messages(messages),
                "temperature": self.temperature,
            }
        )
        self._last_finalize_usage = extract_usage_metrics(response) or None
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return "Model requested an additional tool round; rerun with a larger max_rounds."
        return message.content or "Done."

    async def afinalize_stream(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> AsyncIterator[str]:
        del tool_results
        self._last_stream_usage = None
        stream = await self._acreate_completion(
            {
                "model": self.model,
                "messages": self._to_lmstudio_messages(messages),
                "temperature": self.temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
        )
        async for chunk in stream:
            usage = extract_usage_metrics(chunk)
            if usage:
                self._last_stream_usage = usage
            if not getattr(chunk, "choices", None):
                continue
            content = getattr(chunk.choices[0].delta, "content", None)
            if content:
                yield content
