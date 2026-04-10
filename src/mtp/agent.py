from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import re
from time import perf_counter
from dataclasses import dataclass, field
from collections.abc import AsyncIterator, Iterator
from typing import Any, Callable, Protocol
from uuid import uuid4

from .events import EventStreamContext
from .media import Audio, File, Image, Video
from .prompts import DEFAULT_AUTORESEARCH_SYSTEM_INSTRUCTIONS, DEFAULT_MTP_SYSTEM_INSTRUCTIONS
from .providers.common import ProviderCapabilities, capabilities_from_any
from .session_store import SessionRecord, SessionRun, SessionStore
from .runtime import (
    ExecutionCancelledError,
    RegisteredTool,
    ToolRegistry,
    ToolRetryError,
    ToolStopError,
)
from .tools import tool_spec_from_callable
from .protocol import ExecutionPlan, ToolResult, ToolSpec
from .schema import ToolArgumentsValidationError, validate_tool_arguments
from .strict import validate_strict_dependencies


@dataclass(slots=True)
class AgentAction:
    response_text: str | None = None
    plan: ExecutionPlan | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunOutput:
    run_id: str
    input: str
    final_text: str
    messages: list[dict[str, Any]]
    tool_results: list[ToolResult]
    user_id: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False
    total_tool_calls: int = 0
    output: Any | None = None
    output_validation_error: str | None = None
    paused: bool = False
    pause_reason: str | None = None
    terminated: bool = False
    termination_reason: str | None = None


class ProviderAdapter(Protocol):
    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        ...

    def finalize(
        self,
        messages: list[dict[str, Any]],
        tool_results: list[ToolResult],
    ) -> str:
        ...

    def finalize_stream(
        self,
        messages: list[dict[str, Any]],
        tool_results: list[ToolResult],
    ) -> Iterator[str]:
        ...

    async def anext_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        ...

    async def afinalize(
        self,
        messages: list[dict[str, Any]],
        tool_results: list[ToolResult],
    ) -> str:
        ...


_AGENT_MODES = {"standalone", "member", "delegator", "orchestration"}
_ORCHESTRATOR_MODES = {"delegator", "orchestration"}
_AUTORESEARCH_TERMINATION_PREFIX = "__mtp_autoresearch_terminate__:"
_AUTORESEARCH_DEFAULT_MAX_ROUNDS = 100


class Agent:
    def __init__(
        self,
        provider: ProviderAdapter,
        registry: ToolRegistry | None = None,
        *,
        tools: ToolRegistry | None = None,
        debug_mode: bool = False,
        debug_logger: Callable[[str], None] | None = None,
        debug_max_chars: int = 600,
        strict_dependency_mode: bool = False,
        instructions: str | None = None,
        system_instructions: str | None = None,
        stream_chunk_size: int = 40,
        max_history_messages: int = 200,
        send_media_to_model: bool = True,
        enforce_provider_capabilities: bool = True,
        allow_stream_fallback: bool = True,
        autoresearch: bool = False,
        research_instructions: str | None = None,
        mode: str = "standalone",
        members: dict[str, "Agent"] | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        if registry is not None and tools is not None and registry is not tools:
            raise ValueError("Pass only one of `tools` or `registry`.")
        resolved_tools = tools or registry
        if resolved_tools is None:
            raise ValueError("Missing tools registry. Pass `tools=` (or legacy `registry=`).")

        self.provider = provider
        self.registry = resolved_tools
        self.tools = resolved_tools
        self.debug_mode = debug_mode
        self.debug_logger = debug_logger or print
        self.debug_max_chars = debug_max_chars
        self.strict_dependency_mode = strict_dependency_mode
        self.instructions = instructions
        self.system_instructions = system_instructions or DEFAULT_MTP_SYSTEM_INSTRUCTIONS
        self.stream_chunk_size = stream_chunk_size
        self.max_history_messages = max_history_messages
        self.send_media_to_model = send_media_to_model
        self.enforce_provider_capabilities = enforce_provider_capabilities
        self.allow_stream_fallback = allow_stream_fallback
        self.autoresearch = autoresearch
        self.research_instructions = research_instructions
        self.mode = self._validate_mode(mode)
        self.members: dict[str, Agent] = {}
        self.session_store = session_store
        self._system_seeded = False
        self.messages: list[dict[str, Any]] = []
        self._active_runs: set[str] = set()
        self._cancelled_runs: set[str] = set()
        self._paused_runs: dict[str, RunOutput] = {}
        for name, member in (members or {}).items():
            self.add_member(name, member)
        self._sync_autoresearch_state()

    def _get_provider_capabilities(self) -> ProviderCapabilities | None:
        if not self.enforce_provider_capabilities:
            return None
        caps_fn = getattr(self.provider, "capabilities", None)
        if not callable(caps_fn):
            return None
        try:
            return capabilities_from_any(caps_fn())
        except Exception as exc:  # noqa: BLE001
            self._debug(f"provider capabilities probe failed: {exc}")
            return None

    def _enforce_requested_input_modalities(
        self,
        *,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
    ) -> None:
        if not self.send_media_to_model:
            return
        caps = self._get_provider_capabilities()
        if caps is None:
            return
        requested: list[tuple[str, list[Any] | None]] = [
            ("image", images),
            ("audio", audios),
            ("video", videos),
            ("file", files),
        ]
        unsupported: list[str] = []
        for modality, values in requested:
            if values and not caps.supports_input_modality(modality):
                unsupported.append(modality)
        if not unsupported:
            return
        raise ValueError(
            f"Provider '{caps.provider}' does not support requested input modalities: {sorted(set(unsupported))}. "
            f"Supported modalities: {caps.input_modalities}."
        )

    def _enforce_streaming_request(self, *, stream_requested: bool) -> None:
        if not stream_requested:
            return
        caps = self._get_provider_capabilities()
        if caps is None or caps.supports_finalize_streaming:
            return
        if self.allow_stream_fallback and caps.allow_finalize_stream_fallback:
            self._debug(
                f"provider '{caps.provider}' has no native finalize_stream; using finalize fallback streaming."
            )
            return
        raise ValueError(
            f"Provider '{caps.provider}' does not support native finalize streaming. "
            "Enable `allow_stream_fallback=True` to degrade safely."
        )

    def _validate_mode(self, mode: str) -> str:
        normalized = mode.strip().lower()
        if normalized not in _AGENT_MODES:
            raise ValueError(
                f"Invalid agent mode: {mode!r}. "
                f"Expected one of {sorted(_AGENT_MODES)}."
            )
        return normalized

    def _validate_member_name(self, name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Member name must be a non-empty string.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
            raise ValueError(
                f"Invalid member name: {name!r}. Use only letters, numbers, '_' or '-'."
            )
        return normalized

    def _member_tool_name(self, member_name: str) -> str:
        return f"agent.member.{member_name}"

    def _build_mode_system_instruction(self) -> str | None:
        if self.mode in _ORCHESTRATOR_MODES and self.members:
            tool_names = [self._member_tool_name(name) for name in self.members]
            return (
                "Agent mode is orchestrator/delegator. Delegate focused work to member tools when useful. "
                "Each member tool takes {'task': <string or structured input>, 'max_rounds': <optional int>, "
                "'tool_call_limit': <optional int>} and returns that member's final response text. "
                f"Available member tools: {tool_names}."
            )
        if self.mode == "member":
            return (
                "Agent mode is member. Prioritize handling delegated tasks directly and return concise, actionable output."
            )
        return None

    def _build_autoresearch_system_instruction(self) -> str | None:
        if not self.autoresearch:
            return None
        parts = [DEFAULT_AUTORESEARCH_SYSTEM_INSTRUCTIONS]
        if self.research_instructions:
            parts.append(self.research_instructions.strip())
        return " ".join(part for part in parts if part)

    def _autoresearch_terminate_tool_name(self) -> str:
        return "agent.terminate"

    def _ensure_autoresearch_tool_registered(self) -> None:
        if not self.autoresearch:
            return
        tool_name = self._autoresearch_terminate_tool_name()
        existing_names = {tool.name for tool in self.registry.list_tools()}
        if tool_name in existing_names:
            return
        spec = ToolSpec(
            name=tool_name,
            description=(
                "Terminate an autoresearch run when the user's requirements are satisfied. "
                "Provide a concise reason and final summary."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["reason", "summary"],
                "additionalProperties": False,
            },
            tags=["agent", "autoresearch", "termination"],
        )
        self.registry.add_tool(RegisteredTool(spec=spec, handler=self._autoresearch_terminate))

    def _is_autoresearch_system_message(self, message: dict[str, Any]) -> bool:
        if message.get("role") != "system":
            return False
        content = message.get("content")
        if not isinstance(content, str):
            return False
        return DEFAULT_AUTORESEARCH_SYSTEM_INSTRUCTIONS in content

    def _sync_autoresearch_state(self) -> None:
        tool_name = self._autoresearch_terminate_tool_name()
        if self.autoresearch:
            self._ensure_autoresearch_tool_registered()
        else:
            unregister = getattr(self.registry, "unregister_tool", None)
            if callable(unregister):
                unregister(tool_name)
        if self.messages:
            self.messages = [msg for msg in self.messages if not self._is_autoresearch_system_message(msg)]
        if self.autoresearch and self._system_seeded:
            instruction = self._build_autoresearch_system_instruction()
            if instruction:
                self._append_message({"role": "system", "content": instruction})

    def _autoresearch_terminate(self, reason: str, summary: str) -> str:
        payload = {
            "reason": str(reason or "Task completed."),
            "summary": str(summary or reason or "Task completed."),
        }
        from .exceptions import StopAgentRun

        raise StopAgentRun(_AUTORESEARCH_TERMINATION_PREFIX + json.dumps(payload, ensure_ascii=True))

    def _parse_autoresearch_termination(self, message: str | None) -> dict[str, str] | None:
        if not self.autoresearch or not isinstance(message, str):
            return None
        if not message.startswith(_AUTORESEARCH_TERMINATION_PREFIX):
            return None
        raw_payload = message.removeprefix(_AUTORESEARCH_TERMINATION_PREFIX).strip()
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return {
                "reason": "Autoresearch terminated.",
                "summary": raw_payload or "Autoresearch terminated.",
            }
        reason = str(payload.get("reason") or "Autoresearch terminated.")
        summary = str(payload.get("summary") or reason)
        return {"reason": reason, "summary": summary}

    def _resolve_max_rounds(self, max_rounds: int) -> int:
        if not self.autoresearch:
            return max_rounds
        if max_rounds == 5:
            return _AUTORESEARCH_DEFAULT_MAX_ROUNDS
        return max_rounds

    def _build_member_tool(self, member_name: str, member: "Agent") -> RegisteredTool:
        async def delegate_to_member(
            task: Any,
            max_rounds: int = 5,
            tool_call_limit: int | None = None,
        ) -> str:
            safe_rounds = max(1, int(max_rounds))
            return await member.arun_loop(
                user_input=task,
                max_rounds=safe_rounds,
                tool_call_limit=tool_call_limit,
            )

        spec = ToolSpec(
            name=self._member_tool_name(member_name),
            description=(
                f"Delegate work to member agent '{member_name}'. "
                "Use this for focused subtasks and return its result to continue orchestration."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task": {},
                    "max_rounds": {"type": "integer", "minimum": 1},
                    "tool_call_limit": {"type": "integer", "minimum": 1},
                },
                "required": ["task"],
                "additionalProperties": False,
            },
            tags=["agent", "delegation", "member"],
        )
        return RegisteredTool(spec=spec, handler=delegate_to_member)

    def add_member(self, name: str, member: "Agent") -> None:
        member_name = self._validate_member_name(name)
        if member is self:
            raise ValueError("An agent cannot be registered as its own member.")
        if member_name in self.members:
            raise ValueError(f"Member already registered: {member_name}")
        self.members[member_name] = member
        if self.mode in _ORCHESTRATOR_MODES:
            self.registry.add_tool(self._build_member_tool(member_name, member))

    def set_members(self, members: dict[str, "Agent"]) -> None:
        if self.mode in _ORCHESTRATOR_MODES and self.members:
            raise ValueError(
                "set_members() cannot replace existing members in orchestrator/delegator mode "
                "because delegation tools are already registered. Create a new Agent instance "
                "or call add_member() with new names."
            )
        for name, member in members.items():
            self.add_member(name, member)

    def _debug(self, text: str) -> None:
        if self.debug_mode:
            stamp = datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]
            self.debug_logger(f"DEBUG | [MTP] t={stamp} {text}")

    def cancel_run(self, run_id: str) -> bool:
        if run_id not in self._active_runs:
            return False
        self._cancelled_runs.add(run_id)
        return True

    def add_tool(self, tool: RegisteredTool | Callable[..., Any]) -> None:
        if isinstance(tool, RegisteredTool):
            self.registry.add_tool(tool)
            return
        spec = tool_spec_from_callable(tool)
        self.registry.register_tool(spec, tool)

    def set_tools(self, tools: list[RegisteredTool | Callable[..., Any]]) -> None:
        registered: list[RegisteredTool] = []
        for tool in tools:
            if isinstance(tool, RegisteredTool):
                registered.append(tool)
            else:
                spec = tool_spec_from_callable(tool)
                registered.append(RegisteredTool(spec=spec, handler=tool))
        self.registry.set_tools(registered)

    def _register_run(self, run_id: str) -> None:
        self._active_runs.add(run_id)
        self._cancelled_runs.discard(run_id)

    def _complete_run(self, run_id: str) -> None:
        self._active_runs.discard(run_id)

    def _is_cancelled(self, run_id: str) -> bool:
        return run_id in self._cancelled_runs

    def _short(self, value: Any) -> str:
        try:
            if isinstance(value, str):
                text = value
            else:
                text = json.dumps(value, default=str)
        except Exception:
            text = repr(value)
        if len(text) <= self.debug_max_chars:
            return text
        return text[: self.debug_max_chars] + "...<truncated>"

    def _parse_and_validate_output(
        self,
        final_text: str,
        output_schema: dict[str, Any] | None,
    ) -> tuple[Any | None, str | None]:
        if output_schema is None:
            return None, None
        try:
            parsed = json.loads(final_text)
        except json.JSONDecodeError as exc:
            return None, f"Output is not valid JSON: {exc}"
        try:
            validate_tool_arguments(parsed, output_schema)
        except ToolArgumentsValidationError as exc:
            return parsed, f"Output does not match schema: {exc}"
        return parsed, None

    def _normalize_input(self, user_input: Any) -> Any:
        model_dump = getattr(user_input, "model_dump", None)
        if callable(model_dump):
            return model_dump()
        dict_fn = getattr(user_input, "dict", None)
        if callable(dict_fn):
            return dict_fn()
        return user_input

    def _serialize_input_for_message(self, normalized_input: Any) -> str:
        if isinstance(normalized_input, str):
            return normalized_input
        try:
            return json.dumps(normalized_input, default=str)
        except Exception:
            return str(normalized_input)

    def _coerce_media_context(
        self,
        *,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
    ) -> dict[str, Any] | None:
        if not (images or audios or videos or files):
            return None
        return {
            "images": list(images or []),
            "audios": list(audios or []),
            "videos": list(videos or []),
            "files": list(files or []),
        }

    def _build_user_message(
        self,
        *,
        text: str,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "user", "content": text}
        if self.send_media_to_model:
            if images:
                message["images"] = images
            if audios:
                message["audios"] = audios
            if videos:
                message["videos"] = videos
            if files:
                message["files"] = files
        return message

    def _append_tool_messages_and_media(self, results: list[ToolResult]) -> None:
        tool_messages: list[dict[str, Any]] = []
        all_images: list[Image] = []
        all_audios: list[Audio] = []
        all_videos: list[Video] = []
        all_files: list[File] = []
        for result in results:
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "tool_name": result.tool_name,
                    "content": result.output if result.success else result.error,
                    "success": result.success,
                    "cached": result.cached,
                }
            )
            if result.images:
                all_images.extend(result.images)
            if result.audios:
                all_audios.extend(result.audios)
            if result.videos:
                all_videos.extend(result.videos)
            if result.files:
                all_files.extend(result.files)

        self._extend_messages(tool_messages)
        if self.send_media_to_model and (all_images or all_audios or all_videos or all_files):
            media_message = self._build_user_message(
                text="Take note of the following content",
                images=all_images or None,
                audios=all_audios or None,
                videos=all_videos or None,
                files=all_files or None,
            )
            self._append_message(media_message)

    def _validate_input_schema(
        self,
        normalized_input: Any,
        input_schema: dict[str, Any] | None,
    ) -> str | None:
        if input_schema is None:
            return None
        if not isinstance(normalized_input, dict):
            return "Structured input validation requires dict-like input."
        try:
            validate_tool_arguments(normalized_input, input_schema)
        except ToolArgumentsValidationError as exc:
            return f"Input does not match schema: {exc}"
        return None

    async def _anext_action(self, tools: list[ToolSpec]) -> AgentAction:
        method = getattr(type(self.provider), "anext_action", None)
        if method is not None and method is not ProviderAdapter.anext_action:
            return await self.provider.anext_action(self.messages, tools)  # type: ignore[attr-defined]
        return await asyncio.to_thread(self.provider.next_action, self.messages, tools)

    async def _afinalize(self, tool_results: list[ToolResult]) -> str:
        method = getattr(type(self.provider), "afinalize", None)
        if method is not None and method is not ProviderAdapter.afinalize:
            return await self.provider.afinalize(self.messages, tool_results)  # type: ignore[attr-defined]
        return await asyncio.to_thread(self.provider.finalize, self.messages, tool_results)

    def _build_refiner_messages(self, text: str, prompt: str | None) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if prompt:
            messages.append({"role": "system", "content": prompt})
        messages.append({"role": "user", "content": text})
        return messages

    def _refine_output(
        self,
        text: str,
        *,
        output_model: ProviderAdapter | None = None,
        output_model_prompt: str | None = None,
        parser_model: ProviderAdapter | None = None,
        parser_model_prompt: str | None = None,
    ) -> tuple[str, str | None]:
        current = text
        try:
            if output_model is not None:
                current = output_model.finalize(self._build_refiner_messages(current, output_model_prompt), [])
            if parser_model is not None:
                current = parser_model.finalize(self._build_refiner_messages(current, parser_model_prompt), [])
        except Exception as exc:  # noqa: BLE001
            return text, f"Output model pipeline failed: {exc}"
        return current, None

    async def _arefine_output(
        self,
        text: str,
        *,
        output_model: ProviderAdapter | None = None,
        output_model_prompt: str | None = None,
        parser_model: ProviderAdapter | None = None,
        parser_model_prompt: str | None = None,
    ) -> tuple[str, str | None]:
        current = text
        try:
            if output_model is not None:
                method = getattr(type(output_model), "afinalize", None)
                if method is not None and method is not ProviderAdapter.afinalize:
                    current = await output_model.afinalize(  # type: ignore[attr-defined]
                        self._build_refiner_messages(current, output_model_prompt), []
                    )
                else:
                    current = await asyncio.to_thread(
                        output_model.finalize,
                        self._build_refiner_messages(current, output_model_prompt),
                        [],
                    )
            if parser_model is not None:
                method = getattr(type(parser_model), "afinalize", None)
                if method is not None and method is not ProviderAdapter.afinalize:
                    current = await parser_model.afinalize(  # type: ignore[attr-defined]
                        self._build_refiner_messages(current, parser_model_prompt), []
                    )
                else:
                    current = await asyncio.to_thread(
                        parser_model.finalize,
                        self._build_refiner_messages(current, parser_model_prompt),
                        [],
                    )
        except Exception as exc:  # noqa: BLE001
            return text, f"Output model pipeline failed: {exc}"
        return current, None

    def run(
        self,
        user_input: Any,
        *,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self.run_loop(
            user_input=user_input,
            max_rounds=1,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
        )

    async def arun(
        self,
        user_input: Any,
        *,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return await self.arun_loop(
            user_input=user_input,
            max_rounds=1,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
        )

    def _run_coro_sync(self, coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        close_fn = getattr(coro, "close", None)
        if callable(close_fn):
            close_fn()
        raise RuntimeError(
            "Agent sync APIs cannot run inside an active asyncio event loop. "
            "Use arun()/arun_loop() instead."
        )

    def _trim_messages(self) -> None:
        if self.max_history_messages <= 0 or len(self.messages) <= self.max_history_messages:
            return
        system_messages = [msg for msg in self.messages if msg.get("role") == "system"]
        non_system = [msg for msg in self.messages if msg.get("role") != "system"]
        keep_non_system = max(self.max_history_messages - len(system_messages), 0)
        self.messages = system_messages + non_system[-keep_non_system:]

    def _append_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        self._trim_messages()

    def _extend_messages(self, messages: list[dict[str, Any]]) -> None:
        self.messages.extend(messages)
        self._trim_messages()

    def _chunk_text(self, text: str) -> Iterator[str]:
        if self.stream_chunk_size <= 0:
            yield text
            return
        for i in range(0, len(text), self.stream_chunk_size):
            yield text[i : i + self.stream_chunk_size]

    def _seed_system_messages_if_needed(self) -> None:
        if self._system_seeded:
            return
        if self.system_instructions:
            self._append_message({"role": "system", "content": self.system_instructions})
            self._debug(f"mtp_system_instructions={self._short(self.system_instructions)}")
        if self.instructions:
            self._append_message({"role": "system", "content": self.instructions})
            self._debug(f"user_instructions={self._short(self.instructions)}")
        autoresearch_instruction = self._build_autoresearch_system_instruction()
        if autoresearch_instruction:
            self._append_message({"role": "system", "content": autoresearch_instruction})
            self._debug(f"autoresearch_instructions={self._short(autoresearch_instruction)}")
        mode_instruction = self._build_mode_system_instruction()
        if mode_instruction:
            self._append_message({"role": "system", "content": mode_instruction})
            self._debug(f"mode_instructions={self._short(mode_instruction)}")
        self._system_seeded = True

    def _member_agents_snapshot(self, *, _visited: set[int] | None = None) -> list[dict[str, Any]]:
        visited = set(_visited or set())
        visited.add(id(self))
        members: list[dict[str, Any]] = []
        for member_name, member in self.members.items():
            member_tools = member.registry.list_tools()
            direct_tool_names = [tool.name for tool in member_tools if not tool.name.startswith("agent.member.")]
            delegation_tool_names = [tool.name for tool in member_tools if tool.name.startswith("agent.member.")]
            mode_instruction = member._build_mode_system_instruction()
            nested_members: list[dict[str, Any]] = []
            if id(member) not in visited:
                nested_members = member._member_agents_snapshot(_visited=visited | {id(member)})
            members.append(
                {
                    "id": member_name,
                    "mode": member.mode,
                    "delegation_tool": self._member_tool_name(member_name),
                    "role": member.instructions,
                    "tools": [tool.name for tool in member_tools],
                    "tools_available": len(member_tools),
                    "direct_tool_names": direct_tool_names,
                    "delegation_tool_names": delegation_tool_names,
                    "system_instructions": [member.system_instructions] if member.system_instructions else [],
                    "user_instructions": [member.instructions] if member.instructions else [],
                    "orchestration_instructions": [mode_instruction] if mode_instruction else [],
                    "member_agents": nested_members,
                }
            )
        return members

    def _load_session_history(self, *, session_id: str, user_id: str | None = None) -> None:
        if self.session_store is None:
            return
        stored = self.session_store.get_session(session_id=session_id, user_id=user_id)
        if stored is None:
            self.messages = []
            self._system_seeded = False
            return
        self.messages = [dict(message) for message in stored.messages]
        self._system_seeded = any(msg.get("role") == "system" for msg in self.messages)
        self._sync_autoresearch_state()

    def _save_session_history(
        self,
        *,
        session_id: str | None,
        user_id: str | None,
        metadata: dict[str, Any] | None,
        run: RunOutput | None,
    ) -> None:
        if self.session_store is None or not session_id:
            return
        existing = self.session_store.get_session(session_id=session_id, user_id=user_id)
        run_entries = list(existing.runs) if existing else []
        if run is not None:
            new_run = SessionRun(
                run_id=run.run_id,
                input=run.input,
                final_text=run.final_text,
                cancelled=run.cancelled,
                paused=run.paused,
                total_tool_calls=run.total_tool_calls,
            )
            for idx, item in enumerate(run_entries):
                if item.run_id == new_run.run_id:
                    run_entries[idx] = new_run
                    break
            else:
                run_entries.append(new_run)
        record = SessionRecord(
            session_id=session_id,
            user_id=user_id or (existing.user_id if existing else None),
            metadata=dict(existing.metadata if existing else {}),
            messages=list(self.messages),
            runs=run_entries,
            created_at=existing.created_at if existing else "",
            updated_at="",
        )
        if metadata:
            record.metadata.update(metadata)
        self.session_store.upsert_session(record)

    def _run_tool_rounds(
        self,
        user_text: str | None,
        max_rounds: int,
        *,
        run_id: str,
        tool_call_limit: int | None = None,
        append_user_message: bool = True,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
    ) -> tuple[list[ToolResult], str | None, bool, int, bool, str | None]:
        self._sync_autoresearch_state()
        self._seed_system_messages_if_needed()
        media_context = self._coerce_media_context(images=images, audios=audios, videos=videos, files=files)
        if append_user_message and user_text is not None:
            self._append_message(
                self._build_user_message(
                    text=user_text,
                    images=images,
                    audios=audios,
                    videos=videos,
                    files=files,
                )
            )
            self._debug(f"user_message={user_text!r}")
        tools = self.registry.list_tools()
        self._debug(f"tools_available={len(tools)}")
        self._debug(f"tool_names={[tool.name for tool in tools]}")
        last_results: list[ToolResult] = []
        cancelled = False
        total_tool_calls = 0
        paused = False

        for round_idx in range(1, max_rounds + 1):
            if self._is_cancelled(run_id):
                cancelled = True
                break
            self._debug(f"round={round_idx} start")
            self._debug(
                "llm_request_messages="
                + self._short(
                    [
                        {
                            "role": msg.get("role"),
                            "content": msg.get("content"),
                            "tool_calls": msg.get("tool_calls"),
                            "tool_call_id": msg.get("tool_call_id"),
                            "tool_name": msg.get("tool_name"),
                        }
                        for msg in self.messages
                    ]
                )
            )
            action = self.provider.next_action(self.messages, tools)

            if action.response_text and action.plan is None:
                self._debug("provider returned direct response (no tool plan)")
                self._debug(f"assistant_response={self._short(action.response_text)}")
                self._append_message({"role": "assistant", "content": action.response_text})
                if self.autoresearch:
                    self._debug("autoresearch mode active; continuing after direct response")
                    continue
                return last_results, action.response_text, cancelled, total_tool_calls, paused, None

            if action.plan is None:
                self._debug("provider returned no plan; breaking loop")
                break

            call_count = sum(len(batch.calls) for batch in action.plan.batches)
            if tool_call_limit is not None and total_tool_calls + call_count > tool_call_limit:
                self._append_message(
                    {
                        "role": "system",
                        "content": (
                            "Tool call limit reached. Do not call tools again; provide the best final answer "
                            "from available context."
                        ),
                    }
                )
                break
            total_tool_calls += call_count
            self._debug(f"plan_received batches={len(action.plan.batches)} calls={call_count}")
            for batch_idx, batch in enumerate(action.plan.batches, start=1):
                call_names = [call.name for call in batch.calls]
                self._debug(f"batch#{batch_idx} mode={batch.mode} calls={call_names}")

            if self.strict_dependency_mode:
                violations = validate_strict_dependencies(action.plan)
                if violations:
                    self._debug(f"strict_dependency_violations={len(violations)}")
                    for violation in violations:
                        self._debug(
                            f"strict_violation call_id={violation.call_id} "
                            f"tool={violation.tool_name} message={violation.message}"
                        )
                    self._append_message(
                        {
                            "role": "system",
                            "content": (
                                "Strict dependency mode is enabled. "
                                "Replan tool calls with explicit depends_on and/or $ref "
                                "for multi-call same-toolkit batches."
                            ),
                        }
                    )
                    continue

            assistant_tool_message = action.metadata.get("assistant_tool_message")
            if isinstance(assistant_tool_message, dict):
                self._debug("assistant tool-call message appended")
                self._debug(f"assistant_tool_message={self._short(assistant_tool_message)}")
                self._append_message(assistant_tool_message)

            try:
                last_results = self._run_coro_sync(
                    self.registry.execute_plan(
                        action.plan,
                        cancel_checker=lambda: self._is_cancelled(run_id),
                        media_context=media_context,
                    )
                )
            except ExecutionCancelledError:
                cancelled = True
                break
            except ToolRetryError as exc:
                self._append_message(
                    {
                        "role": "system",
                        "content": (
                            f"Tool {exc.tool_name} requested a retry. "
                            f"Adjust the tool plan and try again. Feedback: {exc.message}"
                        ),
                    }
                )
                continue
            except ToolStopError as exc:
                termination = self._parse_autoresearch_termination(exc.message)
                if termination is not None:
                    return last_results, termination["summary"], cancelled, total_tool_calls, False, termination["reason"]
                paused = True
                return last_results, exc.message, cancelled, total_tool_calls, paused, None
            self._debug(f"executed_calls={len(last_results)}")
            for result in last_results:
                self._debug(
                    "tool_result "
                    f"id={result.call_id} tool={result.tool_name} "
                    f"success={result.success} cached={result.cached} "
                    f"approval={result.approval}"
                )
                self._debug(
                    f"tool_result_payload id={result.call_id} data={self._short(result.output if result.success else result.error)}"
                )
            self._append_tool_messages_and_media(last_results)

        return last_results, None, cancelled, total_tool_calls, paused, None

    def run_output(
        self,
        user_input: Any,
        *,
        max_rounds: int = 5,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        output_model: ProviderAdapter | None = None,
        output_model_prompt: str | None = None,
        parser_model: ProviderAdapter | None = None,
        parser_model_prompt: str | None = None,
    ) -> RunOutput:
        max_rounds = self._resolve_max_rounds(max_rounds)
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        self._enforce_requested_input_modalities(
            images=images,
            audios=audios,
            videos=videos,
            files=files,
        )
        if session_id:
            self._load_session_history(session_id=session_id, user_id=user_id)
        normalized_input = self._normalize_input(user_input)
        input_validation_error = self._validate_input_schema(normalized_input, input_schema)
        serialized_input = self._serialize_input_for_message(normalized_input)
        if input_validation_error is not None:
            run = RunOutput(
                run_id=run_id or str(uuid4()),
                input=serialized_input,
                final_text=input_validation_error,
                messages=list(self.messages),
                tool_results=[],
                user_id=user_id,
                session_id=session_id,
                metadata=dict(metadata or {}),
                output_validation_error=input_validation_error,
            )
            self._save_session_history(
                session_id=session_id,
                user_id=user_id,
                metadata=metadata,
                run=run,
            )
            return run

        resolved_run_id = run_id or str(uuid4())
        self._register_run(resolved_run_id)
        try:
            last_results, direct_response, cancelled, total_tool_calls, paused, termination_reason = self._run_tool_rounds(
                user_text=serialized_input,
                max_rounds=max_rounds,
                run_id=resolved_run_id,
                tool_call_limit=tool_call_limit,
                images=images,
                audios=audios,
                videos=videos,
                files=files,
            )
            if cancelled:
                final_text = "Run cancelled."
                self._append_message({"role": "assistant", "content": final_text})
            elif paused:
                final_text = direct_response or "Run paused."
                self._append_message({"role": "assistant", "content": final_text})
            elif direct_response is not None:
                final_text = direct_response
            else:
                self._debug("calling provider.finalize")
                final_text = self.provider.finalize(self.messages, last_results)
                self._append_message({"role": "assistant", "content": final_text})
                self._debug(f"final response generated text={self._short(final_text)}")

            final_text, refine_error = self._refine_output(
                final_text,
                output_model=output_model,
                output_model_prompt=output_model_prompt,
                parser_model=parser_model,
                parser_model_prompt=parser_model_prompt,
            )
            parsed_output, output_validation_error = self._parse_and_validate_output(final_text, output_schema)
            if refine_error:
                output_validation_error = f"{output_validation_error}; {refine_error}" if output_validation_error else refine_error
            run_output = RunOutput(
                run_id=resolved_run_id,
                input=serialized_input,
                final_text=final_text,
                messages=list(self.messages),
                tool_results=list(last_results),
                user_id=user_id,
                session_id=session_id,
                metadata=dict(metadata or {}),
                cancelled=cancelled,
                total_tool_calls=total_tool_calls,
                output=parsed_output,
                output_validation_error=output_validation_error,
                paused=paused,
                pause_reason=final_text if paused else None,
                terminated=termination_reason is not None,
                termination_reason=termination_reason,
            )
            if paused:
                self._paused_runs[resolved_run_id] = run_output
            else:
                self._paused_runs.pop(resolved_run_id, None)
            self._save_session_history(
                session_id=session_id,
                user_id=user_id,
                metadata=metadata,
                run=run_output,
            )
            return run_output
        finally:
            self._save_session_history(
                session_id=session_id,
                user_id=user_id,
                metadata=metadata,
                run=None,
            )
            self._complete_run(resolved_run_id)

    def run_loop(
        self,
        user_input: Any,
        max_rounds: int = 5,
        *,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        run = self.run_output(
            user_input=user_input,
            max_rounds=max_rounds,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
            input_schema=input_schema,
        )
        return run.final_text

    def continue_run(
        self,
        *,
        run_output: RunOutput | None = None,
        run_id: str | None = None,
        max_rounds: int = 5,
        updated_tools: list[ToolResult] | None = None,
        tool_call_limit: int | None = None,
    ) -> RunOutput:
        max_rounds = self._resolve_max_rounds(max_rounds)
        state = run_output
        if state is None and run_id is not None:
            state = self._paused_runs.get(run_id)
        if state is None:
            raise ValueError("No paused run found. Provide `run_output` or a valid paused `run_id`.")

        self.messages = list(state.messages)
        if updated_tools:
            self._append_tool_messages_and_media(updated_tools)

        resolved_run_id = run_id or state.run_id
        self._register_run(resolved_run_id)
        try:
            last_results, direct_response, cancelled, total_tool_calls, paused, termination_reason = self._run_tool_rounds(
                user_text=None,
                max_rounds=max_rounds,
                run_id=resolved_run_id,
                tool_call_limit=tool_call_limit,
                append_user_message=False,
            )
            if cancelled:
                final_text = "Run cancelled."
                self._append_message({"role": "assistant", "content": final_text})
            elif paused:
                final_text = direct_response or "Run paused."
                self._append_message({"role": "assistant", "content": final_text})
            elif direct_response is not None:
                final_text = direct_response
            else:
                final_text = self.provider.finalize(self.messages, last_results)
                self._append_message({"role": "assistant", "content": final_text})
            continued = RunOutput(
                run_id=resolved_run_id,
                input=state.input,
                final_text=final_text,
                messages=list(self.messages),
                tool_results=list(last_results),
                user_id=state.user_id,
                session_id=state.session_id,
                metadata=dict(state.metadata),
                cancelled=cancelled,
                total_tool_calls=state.total_tool_calls + total_tool_calls,
                paused=paused,
                pause_reason=final_text if paused else None,
                terminated=termination_reason is not None,
                termination_reason=termination_reason,
            )
            if paused:
                self._paused_runs[resolved_run_id] = continued
            else:
                self._paused_runs.pop(resolved_run_id, None)
            self._save_session_history(
                session_id=continued.session_id,
                user_id=continued.user_id,
                metadata=continued.metadata,
                run=continued,
            )
            return continued
        finally:
            self._save_session_history(
                session_id=state.session_id,
                user_id=state.user_id,
                metadata=state.metadata,
                run=None,
            )
            self._complete_run(resolved_run_id)

    async def _arun_tool_rounds(
        self,
        user_text: str | None,
        max_rounds: int,
        *,
        run_id: str,
        tool_call_limit: int | None = None,
        append_user_message: bool = True,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
    ) -> tuple[list[ToolResult], str | None, bool, int, bool, str | None]:
        self._sync_autoresearch_state()
        self._seed_system_messages_if_needed()
        media_context = self._coerce_media_context(images=images, audios=audios, videos=videos, files=files)
        if append_user_message and user_text is not None:
            self._append_message(
                self._build_user_message(
                    text=user_text,
                    images=images,
                    audios=audios,
                    videos=videos,
                    files=files,
                )
            )
        tools = self.registry.list_tools()
        last_results: list[ToolResult] = []
        cancelled = False
        total_tool_calls = 0
        paused = False

        for _round_idx in range(1, max_rounds + 1):
            if self._is_cancelled(run_id):
                cancelled = True
                break
            action = await self._anext_action(tools)
            if action.response_text and action.plan is None:
                self._append_message({"role": "assistant", "content": action.response_text})
                if self.autoresearch:
                    continue
                return last_results, action.response_text, cancelled, total_tool_calls, paused, None
            if action.plan is None:
                break
            call_count = sum(len(batch.calls) for batch in action.plan.batches)
            if tool_call_limit is not None and total_tool_calls + call_count > tool_call_limit:
                self._append_message(
                    {
                        "role": "system",
                        "content": (
                            "Tool call limit reached. Do not call tools again; provide the best final answer "
                            "from available context."
                        ),
                    }
                )
                break
            total_tool_calls += call_count

            if self.strict_dependency_mode:
                violations = validate_strict_dependencies(action.plan)
                if violations:
                    self._append_message(
                        {
                            "role": "system",
                            "content": (
                                "Strict dependency mode is enabled. "
                                "Replan tool calls with explicit depends_on and/or $ref "
                                "for multi-call same-toolkit batches."
                            ),
                        }
                    )
                    continue

            assistant_tool_message = action.metadata.get("assistant_tool_message")
            if isinstance(assistant_tool_message, dict):
                self._append_message(assistant_tool_message)

            try:
                last_results = await self.registry.execute_plan(
                    action.plan,
                    cancel_checker=lambda: self._is_cancelled(run_id),
                    media_context=media_context,
                )
            except ExecutionCancelledError:
                cancelled = True
                break
            except ToolRetryError as exc:
                self._append_message(
                    {
                        "role": "system",
                        "content": (
                            f"Tool {exc.tool_name} requested a retry. "
                            f"Adjust the tool plan and try again. Feedback: {exc.message}"
                        ),
                    }
                )
                continue
            except ToolStopError as exc:
                termination = self._parse_autoresearch_termination(exc.message)
                if termination is not None:
                    return last_results, termination["summary"], cancelled, total_tool_calls, False, termination["reason"]
                paused = True
                return last_results, exc.message, cancelled, total_tool_calls, paused, None
            self._append_tool_messages_and_media(last_results)

        return last_results, None, cancelled, total_tool_calls, paused, None

    async def arun_output(
        self,
        user_input: Any,
        *,
        max_rounds: int = 5,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        output_model: ProviderAdapter | None = None,
        output_model_prompt: str | None = None,
        parser_model: ProviderAdapter | None = None,
        parser_model_prompt: str | None = None,
    ) -> RunOutput:
        max_rounds = self._resolve_max_rounds(max_rounds)
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        self._enforce_requested_input_modalities(
            images=images,
            audios=audios,
            videos=videos,
            files=files,
        )
        if session_id:
            self._load_session_history(session_id=session_id, user_id=user_id)
        normalized_input = self._normalize_input(user_input)
        input_validation_error = self._validate_input_schema(normalized_input, input_schema)
        serialized_input = self._serialize_input_for_message(normalized_input)
        if input_validation_error is not None:
            run = RunOutput(
                run_id=run_id or str(uuid4()),
                input=serialized_input,
                final_text=input_validation_error,
                messages=list(self.messages),
                tool_results=[],
                user_id=user_id,
                session_id=session_id,
                metadata=dict(metadata or {}),
                output_validation_error=input_validation_error,
            )
            self._save_session_history(
                session_id=session_id,
                user_id=user_id,
                metadata=metadata,
                run=run,
            )
            return run

        resolved_run_id = run_id or str(uuid4())
        self._register_run(resolved_run_id)
        try:
            last_results, direct_response, cancelled, total_tool_calls, paused, termination_reason = await self._arun_tool_rounds(
                user_text=serialized_input,
                max_rounds=max_rounds,
                run_id=resolved_run_id,
                tool_call_limit=tool_call_limit,
                images=images,
                audios=audios,
                videos=videos,
                files=files,
            )
            if cancelled:
                final_text = "Run cancelled."
                self._append_message({"role": "assistant", "content": final_text})
            elif paused:
                final_text = direct_response or "Run paused."
                self._append_message({"role": "assistant", "content": final_text})
            elif direct_response is not None:
                final_text = direct_response
            else:
                final_text = await self._afinalize(last_results)
                self._append_message({"role": "assistant", "content": final_text})

            final_text, refine_error = await self._arefine_output(
                final_text,
                output_model=output_model,
                output_model_prompt=output_model_prompt,
                parser_model=parser_model,
                parser_model_prompt=parser_model_prompt,
            )
            parsed_output, output_validation_error = self._parse_and_validate_output(final_text, output_schema)
            if refine_error:
                output_validation_error = f"{output_validation_error}; {refine_error}" if output_validation_error else refine_error
            run_output = RunOutput(
                run_id=resolved_run_id,
                input=serialized_input,
                final_text=final_text,
                messages=list(self.messages),
                tool_results=list(last_results),
                user_id=user_id,
                session_id=session_id,
                metadata=dict(metadata or {}),
                cancelled=cancelled,
                total_tool_calls=total_tool_calls,
                output=parsed_output,
                output_validation_error=output_validation_error,
                paused=paused,
                pause_reason=final_text if paused else None,
                terminated=termination_reason is not None,
                termination_reason=termination_reason,
            )
            if paused:
                self._paused_runs[resolved_run_id] = run_output
            else:
                self._paused_runs.pop(resolved_run_id, None)
            self._save_session_history(
                session_id=session_id,
                user_id=user_id,
                metadata=metadata,
                run=run_output,
            )
            return run_output
        finally:
            self._complete_run(resolved_run_id)

    async def arun_loop(
        self,
        user_input: Any,
        max_rounds: int = 5,
        *,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        run = await self.arun_output(
            user_input=user_input,
            max_rounds=max_rounds,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
            input_schema=input_schema,
        )
        return run.final_text

    async def acontinue_run(
        self,
        *,
        run_output: RunOutput | None = None,
        run_id: str | None = None,
        max_rounds: int = 5,
        updated_tools: list[ToolResult] | None = None,
        tool_call_limit: int | None = None,
    ) -> RunOutput:
        max_rounds = self._resolve_max_rounds(max_rounds)
        state = run_output
        if state is None and run_id is not None:
            state = self._paused_runs.get(run_id)
        if state is None:
            raise ValueError("No paused run found. Provide `run_output` or a valid paused `run_id`.")

        self.messages = list(state.messages)
        if updated_tools:
            self._append_tool_messages_and_media(updated_tools)

        resolved_run_id = run_id or state.run_id
        self._register_run(resolved_run_id)
        try:
            last_results, direct_response, cancelled, total_tool_calls, paused, termination_reason = await self._arun_tool_rounds(
                user_text=None,
                max_rounds=max_rounds,
                run_id=resolved_run_id,
                tool_call_limit=tool_call_limit,
                append_user_message=False,
            )
            if cancelled:
                final_text = "Run cancelled."
                self._append_message({"role": "assistant", "content": final_text})
            elif paused:
                final_text = direct_response or "Run paused."
                self._append_message({"role": "assistant", "content": final_text})
            elif direct_response is not None:
                final_text = direct_response
            else:
                final_text = await self._afinalize(last_results)
                self._append_message({"role": "assistant", "content": final_text})
            continued = RunOutput(
                run_id=resolved_run_id,
                input=state.input,
                final_text=final_text,
                messages=list(self.messages),
                tool_results=list(last_results),
                user_id=state.user_id,
                session_id=state.session_id,
                metadata=dict(state.metadata),
                cancelled=cancelled,
                total_tool_calls=state.total_tool_calls + total_tool_calls,
                paused=paused,
                pause_reason=final_text if paused else None,
                terminated=termination_reason is not None,
                termination_reason=termination_reason,
            )
            if paused:
                self._paused_runs[resolved_run_id] = continued
            else:
                self._paused_runs.pop(resolved_run_id, None)
            self._save_session_history(
                session_id=continued.session_id,
                user_id=continued.user_id,
                metadata=continued.metadata,
                run=continued,
            )
            return continued
        finally:
            self._complete_run(resolved_run_id)

    def run_loop_stream(
        self,
        user_input: Any,
        max_rounds: int = 5,
        *,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        tool_call_limit: int | None = None,
        run_id: str | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        max_rounds = self._resolve_max_rounds(max_rounds)
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        self._enforce_requested_input_modalities(
            images=images,
            audios=audios,
            videos=videos,
            files=files,
        )
        self._enforce_streaming_request(stream_requested=True)
        normalized_input = self._normalize_input(user_input)
        _ = self._validate_input_schema(normalized_input, input_schema)
        serialized_input = self._serialize_input_for_message(normalized_input)
        resolved_run_id = run_id or str(uuid4())
        self._register_run(resolved_run_id)
        try:
            last_results, direct_response, cancelled, _tool_calls, paused, _termination_reason = self._run_tool_rounds(
                user_text=serialized_input,
                max_rounds=max_rounds,
                run_id=resolved_run_id,
                tool_call_limit=tool_call_limit,
                images=images,
                audios=audios,
                videos=videos,
                files=files,
            )
            if cancelled:
                final_text = "Run cancelled."
                self._append_message({"role": "assistant", "content": final_text})
                yield final_text
                return
            if paused:
                final_text = direct_response or "Run paused."
                self._append_message({"role": "assistant", "content": final_text})
                yield final_text
                return
            if direct_response is not None:
                for chunk in self._chunk_text(direct_response):
                    yield chunk
                return

            self._debug("calling provider.finalize_stream")
            finalize_stream = getattr(self.provider, "finalize_stream", None)
            if not callable(finalize_stream):
                final_text = self.provider.finalize(self.messages, last_results)
                self._append_message({"role": "assistant", "content": final_text})
                self._debug(f"final response generated text={self._short(final_text)}")
                yield final_text
                return

            chunks: list[str] = []
            for chunk in finalize_stream(self.messages, last_results):
                if self._is_cancelled(resolved_run_id):
                    final_text = "Run cancelled."
                    self._append_message({"role": "assistant", "content": final_text})
                    yield final_text
                    return
                if chunk:
                    chunks.append(chunk)
                    yield chunk
            final_text = "".join(chunks)
            self._append_message({"role": "assistant", "content": final_text})
            self._debug(f"final streamed response generated text={self._short(final_text)}")
        finally:
            self._complete_run(resolved_run_id)

    def run_loop_events(
        self,
        user_input: Any,
        max_rounds: int = 5,
        *,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        stream_final: bool = True,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        max_rounds = self._resolve_max_rounds(max_rounds)
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        self._enforce_requested_input_modalities(
            images=images,
            audios=audios,
            videos=videos,
            files=files,
        )
        self._enforce_streaming_request(stream_requested=stream_final)
        if session_id:
            self._load_session_history(session_id=session_id, user_id=user_id)
        normalized_input = self._normalize_input(user_input)
        input_validation_error = self._validate_input_schema(normalized_input, input_schema)
        serialized_input = self._serialize_input_for_message(normalized_input)
        media_context = self._coerce_media_context(images=images, audios=audios, videos=videos, files=files)

        resolved_run_id = run_id or str(uuid4())
        self._register_run(resolved_run_id)
        events = EventStreamContext(run_id=resolved_run_id)
        self._sync_autoresearch_state()
        self._seed_system_messages_if_needed()
        self._append_message(
            self._build_user_message(
                text=serialized_input,
                images=images,
                audios=audios,
                videos=videos,
                files=files,
            )
        )
        tools = self.registry.list_tools()
        mode_instruction = self._build_mode_system_instruction()
        system_instructions = [self.system_instructions] if self.system_instructions else []
        user_instructions = [self.instructions] if self.instructions else []
        autoresearch_instruction = self._build_autoresearch_system_instruction()
        research_instructions = [autoresearch_instruction] if autoresearch_instruction else []
        orchestration_instructions = [mode_instruction] if mode_instruction else []
        member_agents = self._member_agents_snapshot()
        direct_tool_names = [tool.name for tool in tools if not tool.name.startswith("agent.member.")]
        delegation_tool_names = [tool.name for tool in tools if tool.name.startswith("agent.member.")]
        yield events.emit(
            "run_started",
            user_message=serialized_input,
            max_rounds=max_rounds,
            tools_available=len(tools),
            tool_names=[tool.name for tool in tools],
            direct_tool_names=direct_tool_names,
            delegation_tool_names=delegation_tool_names,
            system_instructions=system_instructions,
            user_instructions=user_instructions,
            autoresearch=self.autoresearch,
            research_instructions=research_instructions,
            orchestration_instructions=orchestration_instructions,
            member_agents=member_agents,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata or {},
            input_validation_error=input_validation_error,
        )

        last_results: list[ToolResult] = []
        total_tool_calls = 0
        current_round = 0
        try:
            for round_idx in range(1, max_rounds + 1):
                current_round = round_idx
                if self._is_cancelled(resolved_run_id):
                    yield events.emit("run_cancelled", round=round_idx)
                    self._append_message({"role": "assistant", "content": "Run cancelled."})
                    return
                yield events.emit("round_started", round=round_idx)
                llm_started = perf_counter()
                action = self.provider.next_action(self.messages, tools)
                llm_duration = perf_counter() - llm_started
                action_metadata = action.metadata if isinstance(action.metadata, dict) else {}
                yield events.emit(
                    "llm_response",
                    round=round_idx,
                    provider=action_metadata.get("provider"),
                    model=action_metadata.get("model"),
                    usage=action_metadata.get("usage"),
                    rate_limits=action_metadata.get("rate_limits"),
                    duration_seconds=llm_duration,
                    has_plan=action.plan is not None,
                    has_response=bool(action.response_text),
                )

                if action.response_text and action.plan is None:
                    self._append_message({"role": "assistant", "content": action.response_text})
                    if self.autoresearch:
                        if stream_final:
                            for chunk in self._chunk_text(action.response_text):
                                yield events.emit("text_chunk", chunk=chunk, source="direct")
                        continue
                    if stream_final:
                        for chunk in self._chunk_text(action.response_text):
                            yield events.emit("text_chunk", chunk=chunk, source="direct")
                    yield events.emit(
                        "run_completed",
                        final_text=action.response_text,
                        rounds=round_idx,
                        total_tool_calls=total_tool_calls,
                    )
                    return

                if action.plan is None:
                    break

                round_call_count = sum(len(batch.calls) for batch in action.plan.batches)
                if tool_call_limit is not None and total_tool_calls + round_call_count > tool_call_limit:
                    self._append_message(
                        {
                            "role": "system",
                            "content": (
                                "Tool call limit reached. Do not call tools again; provide the best final answer "
                                "from available context."
                            ),
                        }
                    )
                    break
                total_tool_calls += round_call_count

                yield events.emit(
                    "plan_received",
                    round=round_idx,
                    batches=[
                        {
                            "mode": batch.mode,
                            "calls": [call.name for call in batch.calls],
                            "call_ids": [call.id for call in batch.calls],
                        }
                        for batch in action.plan.batches
                    ],
                )

                if self.strict_dependency_mode:
                    violations = validate_strict_dependencies(action.plan)
                    if violations:
                        yield events.emit(
                            "strict_violations",
                            round=round_idx,
                            violations=[
                                {
                                    "call_id": violation.call_id,
                                    "tool_name": violation.tool_name,
                                    "message": violation.message,
                                }
                                for violation in violations
                            ],
                        )
                        self._append_message(
                            {
                                "role": "system",
                                "content": (
                                    "Strict dependency mode is enabled. "
                                    "Replan tool calls with explicit depends_on and/or $ref "
                                    "for multi-call same-toolkit batches."
                                ),
                            }
                        )
                        continue

                assistant_tool_message = action.metadata.get("assistant_tool_message")
                if isinstance(assistant_tool_message, dict):
                    self._append_message(assistant_tool_message)
                    yield events.emit("assistant_tool_message", round=round_idx, message=assistant_tool_message)

                for batch_idx, batch in enumerate(action.plan.batches, start=1):
                    yield events.emit(
                        "batch_started",
                        round=round_idx,
                        batch_index=batch_idx,
                        mode=batch.mode,
                        call_ids=[call.id for call in batch.calls],
                    )
                    for call in batch.calls:
                        yield events.emit(
                            "tool_started",
                            round=round_idx,
                            batch_index=batch_idx,
                            call_id=call.id,
                            tool_name=call.name,
                            arguments=call.arguments,
                            depends_on=call.depends_on,
                        )

                try:
                    last_results = self._run_coro_sync(
                        self.registry.execute_plan(
                            action.plan,
                            cancel_checker=lambda: self._is_cancelled(resolved_run_id),
                            media_context=media_context,
                        )
                    )
                except ExecutionCancelledError:
                    yield events.emit("run_cancelled", round=round_idx)
                    self._append_message({"role": "assistant", "content": "Run cancelled."})
                    return
                except ToolRetryError as exc:
                    self._append_message(
                        {
                            "role": "system",
                            "content": (
                                f"Tool {exc.tool_name} requested a retry. "
                                f"Adjust the tool plan and try again. Feedback: {exc.message}"
                            ),
                        }
                    )
                    yield events.emit("tool_retry_requested", round=round_idx, tool_name=exc.tool_name, feedback=exc.message)
                    continue
                except ToolStopError as exc:
                    termination = self._parse_autoresearch_termination(exc.message)
                    if termination is not None:
                        final_text = termination["summary"]
                        self._append_message({"role": "assistant", "content": final_text})
                        yield events.emit(
                            "run_terminated",
                            round=round_idx,
                            reason=termination["reason"],
                            tool_name=exc.tool_name,
                        )
                        yield events.emit("run_completed", final_text=final_text, rounds=round_idx, total_tool_calls=total_tool_calls)
                        return
                    final_text = exc.message or "Run paused."
                    self._append_message({"role": "assistant", "content": final_text})
                    yield events.emit("run_paused", round=round_idx, reason=final_text, tool_name=exc.tool_name)
                    yield events.emit("run_completed", final_text=final_text, rounds=round_idx, total_tool_calls=total_tool_calls)
                    return
                for result in last_results:
                    yield events.emit(
                        "tool_finished",
                        round=round_idx,
                        call_id=result.call_id,
                        tool_name=result.tool_name,
                        success=result.success,
                        cached=result.cached,
                        approval=result.approval,
                        output=result.output if result.success else None,
                        error=result.error if not result.success else None,
                        media_counts={
                            "images": len(result.images or []),
                            "videos": len(result.videos or []),
                            "audios": len(result.audios or []),
                            "files": len(result.files or []),
                        },
                    )

                self._append_tool_messages_and_media(last_results)

            finalize_stream = getattr(self.provider, "finalize_stream", None)
            if stream_final and callable(finalize_stream):
                chunks: list[str] = []
                finalize_started = perf_counter()
                for chunk in finalize_stream(self.messages, last_results):
                    if self._is_cancelled(resolved_run_id):
                        yield events.emit("run_cancelled", round=max_rounds)
                        self._append_message({"role": "assistant", "content": "Run cancelled."})
                        return
                    if chunk:
                        chunks.append(chunk)
                        yield events.emit("text_chunk", chunk=chunk, source="finalize_stream")
                final_text = "".join(chunks)
                finalize_duration = perf_counter() - finalize_started
            else:
                finalize_started = perf_counter()
                final_text = self.provider.finalize(self.messages, last_results)
                finalize_duration = perf_counter() - finalize_started
                if stream_final:
                    for chunk in self._chunk_text(final_text):
                        yield events.emit("text_chunk", chunk=chunk, source="finalize_fallback")

            finalize_usage = getattr(self.provider, "_last_stream_usage", None)
            if not isinstance(finalize_usage, dict):
                finalize_usage = getattr(self.provider, "_last_finalize_usage", None)
            finalize_rate_limits = getattr(self.provider, "_last_stream_rate_limits", None)
            if not isinstance(finalize_rate_limits, dict):
                finalize_rate_limits = getattr(self.provider, "_last_finalize_rate_limits", None)
            model_name = getattr(self.provider, "model", None) or getattr(self.provider, "model_name", None)
            yield events.emit(
                "llm_response",
                round=max_rounds,
                stage="finalize",
                provider=type(self.provider).__name__,
                model=model_name,
                usage=finalize_usage if isinstance(finalize_usage, dict) else None,
                rate_limits=finalize_rate_limits if isinstance(finalize_rate_limits, dict) else None,
                duration_seconds=finalize_duration,
                has_plan=False,
                has_response=True,
            )
            self._append_message({"role": "assistant", "content": final_text})
            yield events.emit("run_completed", final_text=final_text, rounds=max_rounds, total_tool_calls=total_tool_calls)
        except Exception as exc:  # noqa: BLE001
            yield events.emit(
                "run_failed",
                round=current_round or None,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        finally:
            self._complete_run(resolved_run_id)

    async def arun_loop_events(
        self,
        user_input: Any,
        max_rounds: int = 5,
        *,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        stream_final: bool = True,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        max_rounds = self._resolve_max_rounds(max_rounds)
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        self._enforce_requested_input_modalities(
            images=images,
            audios=audios,
            videos=videos,
            files=files,
        )
        self._enforce_streaming_request(stream_requested=stream_final)
        if session_id:
            self._load_session_history(session_id=session_id, user_id=user_id)
        normalized_input = self._normalize_input(user_input)
        input_validation_error = self._validate_input_schema(normalized_input, input_schema)
        serialized_input = self._serialize_input_for_message(normalized_input)
        media_context = self._coerce_media_context(images=images, audios=audios, videos=videos, files=files)

        resolved_run_id = run_id or str(uuid4())
        self._register_run(resolved_run_id)
        events = EventStreamContext(run_id=resolved_run_id)
        self._sync_autoresearch_state()
        self._seed_system_messages_if_needed()
        self._append_message(
            self._build_user_message(
                text=serialized_input,
                images=images,
                audios=audios,
                videos=videos,
                files=files,
            )
        )
        tools = self.registry.list_tools()
        mode_instruction = self._build_mode_system_instruction()
        system_instructions = [self.system_instructions] if self.system_instructions else []
        user_instructions = [self.instructions] if self.instructions else []
        autoresearch_instruction = self._build_autoresearch_system_instruction()
        research_instructions = [autoresearch_instruction] if autoresearch_instruction else []
        orchestration_instructions = [mode_instruction] if mode_instruction else []
        member_agents = self._member_agents_snapshot()
        direct_tool_names = [tool.name for tool in tools if not tool.name.startswith("agent.member.")]
        delegation_tool_names = [tool.name for tool in tools if tool.name.startswith("agent.member.")]
        yield events.emit(
            "run_started",
            user_message=serialized_input,
            max_rounds=max_rounds,
            tools_available=len(tools),
            tool_names=[tool.name for tool in tools],
            direct_tool_names=direct_tool_names,
            delegation_tool_names=delegation_tool_names,
            system_instructions=system_instructions,
            user_instructions=user_instructions,
            autoresearch=self.autoresearch,
            research_instructions=research_instructions,
            orchestration_instructions=orchestration_instructions,
            member_agents=member_agents,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata or {},
            input_validation_error=input_validation_error,
        )

        last_results: list[ToolResult] = []
        total_tool_calls = 0
        current_round = 0
        try:
            for round_idx in range(1, max_rounds + 1):
                current_round = round_idx
                if self._is_cancelled(resolved_run_id):
                    yield events.emit("run_cancelled", round=round_idx)
                    self._append_message({"role": "assistant", "content": "Run cancelled."})
                    return
                yield events.emit("round_started", round=round_idx)
                llm_started = perf_counter()
                action = await self._anext_action(tools)
                llm_duration = perf_counter() - llm_started
                action_metadata = action.metadata if isinstance(action.metadata, dict) else {}
                yield events.emit(
                    "llm_response",
                    round=round_idx,
                    provider=action_metadata.get("provider"),
                    model=action_metadata.get("model"),
                    usage=action_metadata.get("usage"),
                    rate_limits=action_metadata.get("rate_limits"),
                    duration_seconds=llm_duration,
                    has_plan=action.plan is not None,
                    has_response=bool(action.response_text),
                )

                if action.response_text and action.plan is None:
                    self._append_message({"role": "assistant", "content": action.response_text})
                    if self.autoresearch:
                        if stream_final:
                            for chunk in self._chunk_text(action.response_text):
                                yield events.emit("text_chunk", chunk=chunk, source="direct")
                        continue
                    if stream_final:
                        for chunk in self._chunk_text(action.response_text):
                            yield events.emit("text_chunk", chunk=chunk, source="direct")
                    yield events.emit(
                        "run_completed",
                        final_text=action.response_text,
                        rounds=round_idx,
                        total_tool_calls=total_tool_calls,
                    )
                    return

                if action.plan is None:
                    break

                round_call_count = sum(len(batch.calls) for batch in action.plan.batches)
                if tool_call_limit is not None and total_tool_calls + round_call_count > tool_call_limit:
                    self._append_message(
                        {
                            "role": "system",
                            "content": (
                                "Tool call limit reached. Do not call tools again; provide the best final answer "
                                "from available context."
                            ),
                        }
                    )
                    break
                total_tool_calls += round_call_count

                yield events.emit(
                    "plan_received",
                    round=round_idx,
                    batches=[
                        {
                            "mode": batch.mode,
                            "calls": [call.name for call in batch.calls],
                            "call_ids": [call.id for call in batch.calls],
                        }
                        for batch in action.plan.batches
                    ],
                )

                if self.strict_dependency_mode:
                    violations = validate_strict_dependencies(action.plan)
                    if violations:
                        yield events.emit(
                            "strict_violations",
                            round=round_idx,
                            violations=[
                                {
                                    "call_id": violation.call_id,
                                    "tool_name": violation.tool_name,
                                    "message": violation.message,
                                }
                                for violation in violations
                            ],
                        )
                        self._append_message(
                            {
                                "role": "system",
                                "content": (
                                    "Strict dependency mode is enabled. "
                                    "Replan tool calls with explicit depends_on and/or $ref "
                                    "for multi-call same-toolkit batches."
                                ),
                            }
                        )
                        continue

                assistant_tool_message = action.metadata.get("assistant_tool_message")
                if isinstance(assistant_tool_message, dict):
                    self._append_message(assistant_tool_message)
                    yield events.emit("assistant_tool_message", round=round_idx, message=assistant_tool_message)

                for batch_idx, batch in enumerate(action.plan.batches, start=1):
                    yield events.emit(
                        "batch_started",
                        round=round_idx,
                        batch_index=batch_idx,
                        mode=batch.mode,
                        call_ids=[call.id for call in batch.calls],
                    )
                    for call in batch.calls:
                        yield events.emit(
                            "tool_started",
                            round=round_idx,
                            batch_index=batch_idx,
                            call_id=call.id,
                            tool_name=call.name,
                            arguments=call.arguments,
                            depends_on=call.depends_on,
                        )

                try:
                    last_results = await self.registry.execute_plan(
                        action.plan,
                        cancel_checker=lambda: self._is_cancelled(resolved_run_id),
                        media_context=media_context,
                    )
                except ExecutionCancelledError:
                    yield events.emit("run_cancelled", round=round_idx)
                    self._append_message({"role": "assistant", "content": "Run cancelled."})
                    return
                except ToolRetryError as exc:
                    self._append_message(
                        {
                            "role": "system",
                            "content": (
                                f"Tool {exc.tool_name} requested a retry. "
                                f"Adjust the tool plan and try again. Feedback: {exc.message}"
                            ),
                        }
                    )
                    yield events.emit("tool_retry_requested", round=round_idx, tool_name=exc.tool_name, feedback=exc.message)
                    continue
                except ToolStopError as exc:
                    termination = self._parse_autoresearch_termination(exc.message)
                    if termination is not None:
                        final_text = termination["summary"]
                        self._append_message({"role": "assistant", "content": final_text})
                        yield events.emit(
                            "run_terminated",
                            round=round_idx,
                            reason=termination["reason"],
                            tool_name=exc.tool_name,
                        )
                        yield events.emit("run_completed", final_text=final_text, rounds=round_idx, total_tool_calls=total_tool_calls)
                        return
                    final_text = exc.message or "Run paused."
                    self._append_message({"role": "assistant", "content": final_text})
                    yield events.emit("run_paused", round=round_idx, reason=final_text, tool_name=exc.tool_name)
                    yield events.emit("run_completed", final_text=final_text, rounds=round_idx, total_tool_calls=total_tool_calls)
                    return
                for result in last_results:
                    yield events.emit(
                        "tool_finished",
                        round=round_idx,
                        call_id=result.call_id,
                        tool_name=result.tool_name,
                        success=result.success,
                        cached=result.cached,
                        approval=result.approval,
                        output=result.output if result.success else None,
                        error=result.error if not result.success else None,
                        media_counts={
                            "images": len(result.images or []),
                            "videos": len(result.videos or []),
                            "audios": len(result.audios or []),
                            "files": len(result.files or []),
                        },
                    )

                self._append_tool_messages_and_media(last_results)

            finalize_stream = getattr(self.provider, "finalize_stream", None)
            if stream_final and callable(finalize_stream):
                chunks: list[str] = []
                finalize_started = perf_counter()
                for chunk in finalize_stream(self.messages, last_results):
                    if self._is_cancelled(resolved_run_id):
                        yield events.emit("run_cancelled", round=max_rounds)
                        self._append_message({"role": "assistant", "content": "Run cancelled."})
                        return
                    if chunk:
                        chunks.append(chunk)
                        yield events.emit("text_chunk", chunk=chunk, source="finalize_stream")
                final_text = "".join(chunks)
            else:
                finalize_started = perf_counter()
                final_text = await self._afinalize(last_results)
                finalize_duration = perf_counter() - finalize_started
                if stream_final:
                    for chunk in self._chunk_text(final_text):
                        yield events.emit("text_chunk", chunk=chunk, source="finalize_fallback")
            if stream_final and callable(finalize_stream):
                finalize_duration = perf_counter() - finalize_started

            finalize_usage = getattr(self.provider, "_last_stream_usage", None)
            if not isinstance(finalize_usage, dict):
                finalize_usage = getattr(self.provider, "_last_finalize_usage", None)
            finalize_rate_limits = getattr(self.provider, "_last_stream_rate_limits", None)
            if not isinstance(finalize_rate_limits, dict):
                finalize_rate_limits = getattr(self.provider, "_last_finalize_rate_limits", None)
            model_name = getattr(self.provider, "model", None) or getattr(self.provider, "model_name", None)
            yield events.emit(
                "llm_response",
                round=max_rounds,
                stage="finalize",
                provider=type(self.provider).__name__,
                model=model_name,
                usage=finalize_usage if isinstance(finalize_usage, dict) else None,
                rate_limits=finalize_rate_limits if isinstance(finalize_rate_limits, dict) else None,
                duration_seconds=finalize_duration,
                has_plan=False,
                has_response=True,
            )
            self._append_message({"role": "assistant", "content": final_text})
            yield events.emit("run_completed", final_text=final_text, rounds=max_rounds, total_tool_calls=total_tool_calls)
        except Exception as exc:  # noqa: BLE001
            yield events.emit(
                "run_failed",
                round=current_round or None,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        finally:
            self._complete_run(resolved_run_id)
