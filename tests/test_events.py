from __future__ import annotations

import json
import pytest

from mtp.events import EventStreamContext


class TestEventStreamContext:
    def test_initial_state(self):
        ctx = EventStreamContext()
        assert ctx.sequence == 0
        assert isinstance(ctx.run_id, str)
        assert len(ctx.run_id) > 0

    def test_custom_run_id(self):
        ctx = EventStreamContext(run_id="my-run")
        assert ctx.run_id == "my-run"
        assert ctx.sequence == 0

    def test_emit_increments_sequence(self):
        ctx = EventStreamContext(run_id="r1")
        e1 = ctx.emit("run_started")
        assert e1["sequence"] == 1
        e2 = ctx.emit("round_started")
        assert e2["sequence"] == 2
        e3 = ctx.emit("text_chunk")
        assert e3["sequence"] == 3

    def test_emit_has_envelope_fields(self):
        ctx = EventStreamContext(run_id="r1")
        event = ctx.emit("test_event")
        assert event["type"] == "test_event"
        assert event["run_id"] == "r1"
        assert "timestamp" in event
        assert "sequence" in event

    def test_emit_payload_merged(self):
        ctx = EventStreamContext(run_id="r1")
        event = ctx.emit("tool_finished", tool_name="calc.add", output=42)
        assert event["tool_name"] == "calc.add"
        assert event["output"] == 42

    def test_emit_reserved_keys_not_overwritten(self):
        ctx = EventStreamContext(run_id="r1")
        event = ctx.emit("test", type="hacked", run_id="hacked", sequence=999, timestamp="hacked")
        assert event["type"] == "test"
        assert event["run_id"] == "r1"
        assert event["sequence"] == 1
        assert event["timestamp"] != "hacked"

    def test_emit_data_contains_full_payload(self):
        ctx = EventStreamContext(run_id="r1")
        event = ctx.emit("test", tool_name="calc.add", output=42)
        assert event["data"]["tool_name"] == "calc.add"
        assert event["data"]["output"] == 42

    def test_emit_unique_run_ids(self):
        ctx1 = EventStreamContext()
        ctx2 = EventStreamContext()
        assert ctx1.run_id != ctx2.run_id

    def test_timestamp_is_iso_format(self):
        ctx = EventStreamContext(run_id="r1")
        event = ctx.emit("test")
        ts = event["timestamp"]
        assert "T" in ts
        parsed = __import__("datetime").datetime.fromisoformat(ts)
        assert parsed is not None
