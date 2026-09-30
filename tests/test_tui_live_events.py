from __future__ import annotations

import threading
import time

from mtp.cli.tui_live_events import LiveEventBatcher


def test_adjacent_text_chunks_are_coalesced() -> None:
    batches: list[list[tuple[str, object]]] = []
    batcher = LiveEventBatcher(batches.append, interval=10)
    for token in ["Hel", "lo", " world"]:
        batcher.emit("text", token)
    batcher.close()
    assert batches == [[("text", "Hello world")]]


def test_non_text_event_flushes_immediately_and_keeps_order() -> None:
    batches: list[list[tuple[str, object]]] = []
    batcher = LiveEventBatcher(batches.append, interval=10)
    batcher.emit("reasoning", "think")
    batcher.emit("text", "a")
    batcher.emit("tool", "fs.read")
    assert batches == [[("reasoning", "think"), ("text", "a"), ("tool", "fs.read")]]
    batcher.emit("text", "b")
    batcher.close()
    assert batches[-1] == [("text", "b")]


def test_flusher_delivers_text_without_further_events() -> None:
    batches: list[list[tuple[str, object]]] = []
    delivered = threading.Event()

    def sink(batch: list[tuple[str, object]]) -> None:
        batches.append(batch)
        delivered.set()

    batcher = LiveEventBatcher(sink, interval=0.02)
    batcher.emit("text", "stalled stream")
    assert delivered.wait(2)
    assert batches == [[("text", "stalled stream")]]
    batcher.close()


def test_events_after_close_are_dropped() -> None:
    batches: list[list[tuple[str, object]]] = []
    batcher = LiveEventBatcher(batches.append)
    batcher.close()
    batcher.emit("text", "late")
    batcher.emit("tool", "late")
    time.sleep(0.05)
    assert batches == []


def test_concurrent_producer_and_flusher_preserve_text() -> None:
    received: list[str] = []
    batcher = LiveEventBatcher(
        lambda batch: received.extend(str(m) for k, m in batch if k == "text"), interval=0.001
    )
    tokens = [f"{i}," for i in range(2000)]
    for token in tokens:
        batcher.emit("text", token)
    batcher.close()
    assert "".join(received) == "".join(tokens)
