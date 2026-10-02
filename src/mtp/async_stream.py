"""Bridge blocking SDK iterators without putting StopIteration in asyncio Futures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from typing import TypeVar, cast

T = TypeVar("T")


def _next_item(iterator: Iterator[T]) -> tuple[bool, T | None]:
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


async def async_from_sync(factory: Callable[[], Iterator[T]]) -> AsyncIterator[T]:
    iterator = await asyncio.to_thread(factory)
    try:
        while True:
            available, value = await asyncio.to_thread(_next_item, iterator)
            if not available:
                return
            yield cast(T, value)
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            try:
                await asyncio.to_thread(close)
            except ValueError as exc:
                # Cancellation cannot stop an SDK read already running in its worker.
                # That worker still owns the generator until its read returns.
                if "already executing" not in str(exc):
                    raise
