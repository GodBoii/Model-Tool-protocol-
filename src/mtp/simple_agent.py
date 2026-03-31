from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
import json
from typing import Any

from .agent import Agent
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
        registry: ToolRegistry,
        debug_mode: bool = False,
        strict_dependency_mode: bool = False,
        instructions: str | None = None,
        system_instructions: str | None = None,
        stream_chunk_size: int = 40,
        max_history_messages: int = 200,
    ) -> None:
        self._agent = Agent(
            provider=provider,
            registry=registry,
            debug_mode=debug_mode,
            strict_dependency_mode=strict_dependency_mode,
            instructions=instructions,
            system_instructions=system_instructions,
            stream_chunk_size=stream_chunk_size,
            max_history_messages=max_history_messages,
        )

    def run(self, prompt: str, *, max_rounds: int = 5) -> str:
        return self._agent.run_loop(prompt, max_rounds=max_rounds)

    def run_stream(self, prompt: str, *, max_rounds: int = 5) -> Iterator[str]:
        return self._agent.run_loop_stream(prompt, max_rounds=max_rounds)

    async def arun(self, prompt: str, *, max_rounds: int = 5) -> str:
        return await self._agent.arun_loop(prompt, max_rounds=max_rounds)

    def run_events(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        stream_final: bool = True,
    ) -> Iterator[dict[str, Any]]:
        return self._agent.run_loop_events(prompt, max_rounds=max_rounds, stream_final=stream_final)

    def arun_events(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        stream_final: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        return self._agent.arun_loop_events(prompt, max_rounds=max_rounds, stream_final=stream_final)

    def print_response(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        stream: bool = False,
        stream_events: bool = False,
    ) -> None:
        if stream_events:
            for event in self.run_events(prompt, max_rounds=max_rounds, stream_final=stream):
                print(json.dumps(event, default=str))
            return
        if not stream:
            print(self.run(prompt, max_rounds=max_rounds))
            return
        for chunk in self.run_stream(prompt, max_rounds=max_rounds):
            print(chunk, end="", flush=True)
        print()
