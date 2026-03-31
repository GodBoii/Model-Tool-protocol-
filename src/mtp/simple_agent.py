from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import json
from typing import Any

from .agent import Agent
from .config import load_dotenv_if_available
from .providers import GroqToolCallingProvider
from .runtime import ToolRegistry
from .toolkits import register_local_toolkits


class MTPAgent:
    """
    High-level convenience API for common single-agent usage.
    """

    def __init__(
        self,
        *,
        model: str = "llama-3.3-70b-versatile",
        instructions: str | None = None,
        debug_mode: bool = False,
        strict_dependency_mode: bool = False,
        base_dir: str | Path = ".",
        load_dotenv: bool = True,
        stream_chunk_size: int = 40,
    ) -> None:
        if load_dotenv:
            load_dotenv_if_available()

        registry = ToolRegistry()
        register_local_toolkits(registry, base_dir=base_dir)

        provider = GroqToolCallingProvider(
            model=model,
            strict_dependency_mode=strict_dependency_mode,
        )
        self._agent = Agent(
            provider=provider,
            registry=registry,
            debug_mode=debug_mode,
            strict_dependency_mode=strict_dependency_mode,
            instructions=instructions,
            stream_chunk_size=stream_chunk_size,
        )

    def run(self, prompt: str, *, max_rounds: int = 5) -> str:
        return self._agent.run_loop(prompt, max_rounds=max_rounds)

    def run_stream(self, prompt: str, *, max_rounds: int = 5) -> Iterator[str]:
        return self._agent.run_loop_stream(prompt, max_rounds=max_rounds)

    def run_events(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        stream_final: bool = True,
    ) -> Iterator[dict[str, Any]]:
        return self._agent.run_loop_events(prompt, max_rounds=max_rounds, stream_final=stream_final)

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
