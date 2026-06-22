from __future__ import annotations

import json
import pytest
from pathlib import Path

from mtp.session_store import (
    JsonSessionStore,
    SessionRecord,
    SessionRun,
    _json_safe,
    _utc_now_iso,
    _validate_sql_identifier,
    _parse_json_blob,
)


class TestSessionRun:
    def test_defaults(self):
        run = SessionRun(run_id="r1", input="hello", final_text="hi")
        assert run.run_id == "r1"
        assert run.input == "hello"
        assert run.final_text == "hi"
        assert run.cancelled is False
        assert run.paused is False
        assert run.total_tool_calls == 0
        assert isinstance(run.created_at, str)


class TestSessionRecord:
    def test_defaults(self):
        record = SessionRecord(session_id="s1")
        assert record.session_id == "s1"
        assert record.user_id is None
        assert record.messages == []
        assert record.runs == []

    def test_to_dict(self):
        record = SessionRecord(session_id="s1", user_id="u1")
        d = record.to_dict()
        assert d["session_id"] == "s1"
        assert d["user_id"] == "u1"

    def test_from_dict(self):
        d = {
            "session_id": "s1",
            "user_id": "u1",
            "messages": [{"role": "user", "content": "hi"}],
            "runs": [{"run_id": "r1", "input": "hi", "final_text": "hello"}],
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }
        record = SessionRecord.from_dict(d)
        assert record.session_id == "s1"
        assert len(record.messages) == 1
        assert len(record.runs) == 1
        assert record.runs[0].run_id == "r1"

    def test_from_dict_minimal(self):
        record = SessionRecord.from_dict({"session_id": "s1"})
        assert record.session_id == "s1"
        assert record.messages == []

    def test_roundtrip(self):
        record = SessionRecord(
            session_id="s1",
            user_id="u1",
            messages=[{"role": "user", "content": "hi"}],
            runs=[SessionRun(run_id="r1", input="hi", final_text="hello")],
        )
        d = record.to_dict()
        restored = SessionRecord.from_dict(d)
        assert restored.session_id == record.session_id
        assert len(restored.messages) == len(record.messages)
        assert len(restored.runs) == len(record.runs)


class TestJsonSafe:
    def test_primitives(self):
        assert _json_safe("s") == "s"
        assert _json_safe(42) == 42
        assert _json_safe(3.14) == 3.14
        assert _json_safe(True) is True
        assert _json_safe(None) is None

    def test_bytes(self):
        assert _json_safe(b"hello") == "hello"

    def test_path(self):
        result = _json_safe(Path("/tmp/test"))
        assert isinstance(result, str)
        assert "tmp" in result
        assert "test" in result

    def test_dict(self):
        assert _json_safe({"a": 1}) == {"a": 1}

    def test_list(self):
        assert _json_safe([1, "s"]) == [1, "s"]

    def test_dataclass(self):
        from dataclasses import dataclass
        @dataclass
        class DC:
            x: int = 1
        result = _json_safe(DC())
        assert result == {"x": 1}


class TestJsonSessionStore:
    def test_upsert_and_get(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        record = SessionRecord(session_id="s1", user_id="u1")
        result = store.upsert_session(record)
        assert result.session_id == "s1"
        fetched = store.get_session("s1", user_id="u1")
        assert fetched is not None
        assert fetched.session_id == "s1"

    def test_get_nonexistent(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        assert store.get_session("nonexistent") is None

    def test_update_existing(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        record = SessionRecord(session_id="s1", messages=[])
        store.upsert_session(record)
        record.messages.append({"role": "user", "content": "hi"})
        store.upsert_session(record)
        fetched = store.get_session("s1")
        assert len(fetched.messages) == 1

    def test_user_id_filter(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        record = SessionRecord(session_id="s1", user_id="u1")
        store.upsert_session(record)
        assert store.get_session("s1", user_id="u1") is not None
        assert store.get_session("s1", user_id="u2") is None
        assert store.get_session("s1") is None

    def test_multiple_sessions(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        store.upsert_session(SessionRecord(session_id="s1"))
        store.upsert_session(SessionRecord(session_id="s2"))
        assert store.get_session("s1") is not None
        assert store.get_session("s2") is not None

    def test_with_runs(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        record = SessionRecord(
            session_id="s1",
            runs=[SessionRun(run_id="r1", input="hi", final_text="hello")],
        )
        store.upsert_session(record)
        fetched = store.get_session("s1")
        assert len(fetched.runs) == 1
        assert fetched.runs[0].run_id == "r1"


class TestValidateSqlIdentifier:
    def test_valid(self):
        assert _validate_sql_identifier("my_table") == "my_table"
        assert _validate_sql_identifier("Table1") == "Table1"

    def test_invalid(self):
        import pytest
        with pytest.raises(ValueError, match="Invalid"):
            _validate_sql_identifier("my-table")
        with pytest.raises(ValueError, match="Invalid"):
            _validate_sql_identifier("1table")
        with pytest.raises(ValueError, match="Invalid"):
            _validate_sql_identifier("table; DROP")


class TestParseJsonBlob:
    def test_none(self):
        assert _parse_json_blob(None, fallback=[]) == []

    def test_dict(self):
        assert _parse_json_blob({"a": 1}, fallback={}) == {"a": 1}

    def test_list(self):
        assert _parse_json_blob([1, 2], fallback=[]) == [1, 2]

    def test_string(self):
        assert _parse_json_blob('{"a": 1}', fallback={}) == {"a": 1}

    def test_empty_string(self):
        assert _parse_json_blob("", fallback=[]) == []

    def test_invalid_json(self):
        assert _parse_json_blob("not json", fallback=[]) == []

    def test_bytes(self):
        assert _parse_json_blob(b'{"a": 1}', fallback={}) == {"a": 1}


class TestUtcNowIso:
    def test_returns_iso(self):
        ts = _utc_now_iso()
        assert "T" in ts
        assert "+" in ts or "Z" in ts or ts.endswith("+00:00")
