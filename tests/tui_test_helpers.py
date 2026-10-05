"""Wait for observable UI readiness rather than a fixed runner-dependent delay."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from textual.pilot import Pilot


async def wait_until(pilot: Pilot, ready: Callable[[], bool], timeout: float = 3) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not ready():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("TUI did not reach the expected state before the deadline.")
        await pilot.pause(0.01)
