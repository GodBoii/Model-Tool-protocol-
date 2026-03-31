from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
import json
from typing import Any

from .agent import Agent, RunOutput
from .runtime import ToolRegistry
from .agent import ProviderAdapter


class MTPAgent:
    """
    Provider-agnostic convenience wrapper around Agent.
    Requires explicit provider + tool registry from the user.
    """

    def __init__(
        self,
        *,
        provider: ProviderAdapter,
        tools: ToolRegistry | None = None,
        registry: ToolRegistry | None = None,
        debug_mode: bool = False,
        strict_dependency_mode: bool = False,
        instructions: str | None = None,
        system_instructions: str | None = None,
        stream_chunk_size: int = 40,
        max_history_messages: int = 200,
    ) -> None:
        if registry is not None and tools is not None and registry is not tools:
            raise ValueError("Pass only one of `tools` or `registry`.")
        resolved_tools = tools or registry
        if resolved_tools is None:
            raise ValueError("Missing tools registry. Pass `tools=` (or legacy `registry=`).")

        self._agent = Agent(
            provider=provider,
            tools=resolved_tools,
            debug_mode=debug_mode,
            strict_dependency_mode=strict_dependency_mode,
            instructions=instructions,
            system_instructions=system_instructions,
            stream_chunk_size=stream_chunk_size,
            max_history_messages=max_history_messages,
        )

    def run(self, prompt: str, *, max_rounds: int = 5, tool_call_limit: int | None = None) -> str:
        return self._agent.run_loop(prompt, max_rounds=max_rounds, tool_call_limit=tool_call_limit)

    def run_output(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> RunOutput:
        return self._agent.run_output(
            prompt,
            max_rounds=max_rounds,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
            output_schema=output_schema,
        )

    def run_stream(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        tool_call_limit: int | None = None,
        run_id: str | None = None,
    ) -> Iterator[str]:
        return self._agent.run_loop_stream(
            prompt,
            max_rounds=max_rounds,
            tool_call_limit=tool_call_limit,
            run_id=run_id,
        )

    async def arun(self, prompt: str, *, max_rounds: int = 5, tool_call_limit: int | None = None) -> str:
        return await self._agent.arun_loop(prompt, max_rounds=max_rounds, tool_call_limit=tool_call_limit)

    async def arun_output(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> RunOutput:
        return await self._agent.arun_output(
            prompt,
            max_rounds=max_rounds,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
            output_schema=output_schema,
        )

    def run_events(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        stream_final: bool = True,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._agent.run_loop_events(
            prompt,
            max_rounds=max_rounds,
            stream_final=stream_final,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
        )

    def arun_events(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        stream_final: bool = True,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        return self._agent.arun_loop_events(
            prompt,
            max_rounds=max_rounds,
            stream_final=stream_final,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
        )

    def cancel_run(self, run_id: str) -> bool:
        return self._agent.cancel_run(run_id)

    def print_response(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        stream: bool = False,
        stream_events: bool = False,
        run_id: str | None = None,
        tool_call_limit: int | None = None,
    ) -> None:
        if stream_events:
            for event in self.run_events(
                prompt,
                max_rounds=max_rounds,
                stream_final=stream,
                run_id=run_id,
                tool_call_limit=tool_call_limit,
            ):
                print(json.dumps(event, default=str))
            return
        if not stream:
            print(self.run(prompt, max_rounds=max_rounds, tool_call_limit=tool_call_limit))
            return
        for chunk in self.run_stream(
            prompt,
            max_rounds=max_rounds,
            tool_call_limit=tool_call_limit,
            run_id=run_id,
        ):
            print(chunk, end="", flush=True)
        print()
