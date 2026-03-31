from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from collections.abc import Iterator
from typing import Any, Callable, Protocol

from .events import EventStreamContext
from .prompts import DEFAULT_MTP_SYSTEM_INSTRUCTIONS
from .protocol import ExecutionPlan, ToolResult, ToolSpec
from .runtime import ToolRegistry
from .strict import validate_strict_dependencies


@dataclass(slots=True)
class AgentAction:
    response_text: str | None = None
    plan: ExecutionPlan | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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


class Agent:
    def __init__(
        self,
        provider: ProviderAdapter,
        registry: ToolRegistry,
        *,
        debug_mode: bool = False,
        debug_logger: Callable[[str], None] | None = None,
        debug_max_chars: int = 600,
        strict_dependency_mode: bool = False,
        instructions: str | None = None,
        system_instructions: str | None = None,
        stream_chunk_size: int = 40,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.debug_mode = debug_mode
        self.debug_logger = debug_logger or print
        self.debug_max_chars = debug_max_chars
        self.strict_dependency_mode = strict_dependency_mode
        self.instructions = instructions
        self.system_instructions = system_instructions or DEFAULT_MTP_SYSTEM_INSTRUCTIONS
        self.stream_chunk_size = stream_chunk_size
        self._system_seeded = False
        self.messages: list[dict[str, Any]] = []

    def _debug(self, text: str) -> None:
        if self.debug_mode:
            self.debug_logger(f"[MTP DEBUG] {text}")

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

    def run(self, user_text: str) -> str:
        return self.run_loop(user_text=user_text, max_rounds=1)

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
            self.messages.append({"role": "system", "content": self.system_instructions})
            self._debug(f"mtp_system_instructions={self._short(self.system_instructions)}")
        if self.instructions:
            self.messages.append({"role": "system", "content": self.instructions})
            self._debug(f"user_instructions={self._short(self.instructions)}")
        self._system_seeded = True

    def _run_tool_rounds(self, user_text: str, max_rounds: int) -> tuple[list[ToolResult], str | None]:
        self._seed_system_messages_if_needed()

        self.messages.append({"role": "user", "content": user_text})
        self._debug(f"user_message={user_text!r}")
        tools = self.registry.list_tools()
        self._debug(f"tools_available={len(tools)}")
        self._debug(f"tool_names={[tool.name for tool in tools]}")
        last_results: list[ToolResult] = []

        for round_idx in range(1, max_rounds + 1):
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
                self.messages.append({"role": "assistant", "content": action.response_text})
                return last_results, action.response_text

            if action.plan is None:
                self._debug("provider returned no plan; breaking loop")
                break

            call_count = sum(len(batch.calls) for batch in action.plan.batches)
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
                    self.messages.append(
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
                self.messages.append(assistant_tool_message)

            last_results = asyncio.run(self.registry.execute_plan(action.plan))
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
            tool_messages = [
                {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "tool_name": result.tool_name,
                    "content": result.output if result.success else result.error,
                    "success": result.success,
                    "cached": result.cached,
                }
                for result in last_results
            ]
            self.messages.extend(tool_messages)

        return last_results, None

    def run_loop(self, user_text: str, max_rounds: int = 5) -> str:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")

        last_results, direct_response = self._run_tool_rounds(user_text=user_text, max_rounds=max_rounds)
        if direct_response is not None:
            return direct_response

        self._debug("calling provider.finalize")
        final_text = self.provider.finalize(self.messages, last_results)
        self.messages.append({"role": "assistant", "content": final_text})
        self._debug(f"final response generated text={self._short(final_text)}")
        return final_text

    def run_loop_stream(self, user_text: str, max_rounds: int = 5) -> Iterator[str]:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")

        last_results, direct_response = self._run_tool_rounds(user_text=user_text, max_rounds=max_rounds)
        if direct_response is not None:
            for chunk in self._chunk_text(direct_response):
                yield chunk
            return

        self._debug("calling provider.finalize_stream")
        finalize_stream = getattr(self.provider, "finalize_stream", None)
        if not callable(finalize_stream):
            final_text = self.provider.finalize(self.messages, last_results)
            self.messages.append({"role": "assistant", "content": final_text})
            self._debug(f"final response generated text={self._short(final_text)}")
            yield final_text
            return

        chunks: list[str] = []
        for chunk in finalize_stream(self.messages, last_results):
            if chunk:
                chunks.append(chunk)
                yield chunk
        final_text = "".join(chunks)
        self.messages.append({"role": "assistant", "content": final_text})
        self._debug(f"final streamed response generated text={self._short(final_text)}")

    def run_loop_events(
        self,
        user_text: str,
        max_rounds: int = 5,
        *,
        stream_final: bool = True,
    ) -> Iterator[dict[str, Any]]:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")

        events = EventStreamContext()
        self._seed_system_messages_if_needed()
        self.messages.append({"role": "user", "content": user_text})
        tools = self.registry.list_tools()
        yield events.emit(
            "run_started",
            user_message=user_text,
            max_rounds=max_rounds,
            tools_available=len(tools),
            tool_names=[tool.name for tool in tools],
        )

        last_results: list[ToolResult] = []
        for round_idx in range(1, max_rounds + 1):
            yield events.emit("round_started", round=round_idx)
            action = self.provider.next_action(self.messages, tools)

            if action.response_text and action.plan is None:
                self.messages.append({"role": "assistant", "content": action.response_text})
                if stream_final:
                    for chunk in self._chunk_text(action.response_text):
                        yield events.emit("text_chunk", chunk=chunk, source="direct")
                yield events.emit("run_completed", final_text=action.response_text, rounds=round_idx)
                return

            if action.plan is None:
                break

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
                    self.messages.append(
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
                self.messages.append(assistant_tool_message)
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

            last_results = asyncio.run(self.registry.execute_plan(action.plan))
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
                )

            tool_messages = [
                {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "tool_name": result.tool_name,
                    "content": result.output if result.success else result.error,
                    "success": result.success,
                    "cached": result.cached,
                }
                for result in last_results
            ]
            self.messages.extend(tool_messages)

        finalize_stream = getattr(self.provider, "finalize_stream", None)
        if stream_final and callable(finalize_stream):
            chunks: list[str] = []
            for chunk in finalize_stream(self.messages, last_results):
                if chunk:
                    chunks.append(chunk)
                    yield events.emit("text_chunk", chunk=chunk, source="finalize_stream")
            final_text = "".join(chunks)
        else:
            final_text = self.provider.finalize(self.messages, last_results)
            if stream_final:
                for chunk in self._chunk_text(final_text):
                    yield events.emit("text_chunk", chunk=chunk, source="finalize_fallback")

        self.messages.append({"role": "assistant", "content": final_text})
        yield events.emit("run_completed", final_text=final_text, rounds=max_rounds)
