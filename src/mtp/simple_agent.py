from __future__ import annotations

from pathlib import Path

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
        )

    def run(self, prompt: str, *, max_rounds: int = 5) -> str:
        return self._agent.run_loop(prompt, max_rounds=max_rounds)

    def print_response(self, prompt: str, *, max_rounds: int = 5) -> None:
        print(self.run(prompt, max_rounds=max_rounds))
