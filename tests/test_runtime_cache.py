from __future__ import annotations

import asyncio

from mtp.protocol import ToolCall, ToolSpec
from mtp.runtime import ToolRegistry


def _cached_spec(name: str = "test.cached", *, ttl: int = 60) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="cache test tool",
        input_schema={"type": "object", "additionalProperties": False},
        cache_ttl_seconds=ttl,
    )


def _execute(registry: ToolRegistry, call_id: str, name: str = "test.cached"):
    return asyncio.run(registry.execute_call(ToolCall(call_id, name), {}))


def test_cached_mutable_results_are_isolated_from_callers() -> None:
    registry = ToolRegistry()
    invocations = 0

    def handler() -> dict[str, list[str]]:
        nonlocal invocations
        invocations += 1
        return {"items": ["original"]}

    registry.register_tool(_cached_spec(), handler)

    first = _execute(registry, "first")
    first.output["items"].append("mutated")
    second = _execute(registry, "second")
    second.output["items"].append("also-mutated")
    third = _execute(registry, "third")

    assert invocations == 1
    assert second.cached is True
    assert third.output == {"items": ["original"]}


def test_cache_clear_can_target_one_tool_or_all_tools() -> None:
    registry = ToolRegistry()
    registry.register_tool(_cached_spec("test.one"), lambda: "one")
    registry.register_tool(_cached_spec("test.two"), lambda: "two")
    _execute(registry, "one", "test.one")
    _execute(registry, "two", "test.two")

    assert registry.clear_cache("test.one") == 1
    assert registry.cache_stats()["entries"] == 1
    assert registry.clear_cache("missing") == 0
    assert registry.clear_cache() == 1
    assert registry.cache_stats()["entries"] == 0


def test_cache_stats_track_hits_misses_evictions_and_reset() -> None:
    registry = ToolRegistry(max_cache_entries=1)
    registry.register_tool(_cached_spec("test.one"), lambda: "one")
    registry.register_tool(_cached_spec("test.two"), lambda: "two")

    _execute(registry, "one-miss", "test.one")
    _execute(registry, "one-hit", "test.one")
    _execute(registry, "two-miss", "test.two")

    assert registry.cache_stats() == {
        "entries": 1,
        "max_entries": 1,
        "hits": 1,
        "misses": 2,
        "evictions": 1,
    }
    snapshot = registry.cache_stats(reset_stats=True)
    assert snapshot["hits"] == 1
    assert registry.cache_stats()["hits"] == 0
    assert registry.cache_stats()["evictions"] == 0


def test_expiration_uses_monotonic_deadline(monkeypatch) -> None:
    registry = ToolRegistry()
    invocations = 0
    monotonic_now = 100.0

    def fake_monotonic() -> float:
        return monotonic_now

    def handler() -> int:
        nonlocal invocations
        invocations += 1
        return invocations

    monkeypatch.setattr("mtp.runtime.time.monotonic", fake_monotonic)
    registry.register_tool(_cached_spec(ttl=5), handler)
    assert _execute(registry, "first").output == 1

    monotonic_now = 104.9
    assert _execute(registry, "hit").cached is True
    monotonic_now = 105.0
    assert _execute(registry, "expired").output == 2
    assert invocations == 2

