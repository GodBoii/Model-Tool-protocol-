from __future__ import annotations

import json
import os
import time
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
    def test_rejects_nonpositive_store_limit(self, tmp_path):
        with pytest.raises(ValueError, match="positive"):
            JsonSessionStore(db_path=tmp_path, max_store_bytes=0)

    def test_refuses_oversized_store_before_reading(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path, max_store_bytes=8)
        store.db_path.mkdir(parents=True, exist_ok=True)
        store.file_path.write_text("[" + " " * 20 + "]", encoding="utf-8")

        with pytest.raises(ValueError, match="safety limit"):
            store.list_sessions()

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

    def test_lock_file_removed_after_access(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        store.upsert_session(SessionRecord(session_id="s1"))
        assert not store.lock_path.exists()

    def test_same_session_id_is_isolated_by_user(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        store.upsert_session(SessionRecord(session_id="shared", user_id="alice", metadata={"n": 1}))
        store.upsert_session(SessionRecord(session_id="shared", user_id="bob", metadata={"n": 2}))

        assert store.get_session("shared", user_id="alice").metadata == {"n": 1}
        assert store.get_session("shared", user_id="bob").metadata == {"n": 2}

    def test_list_sessions_filters_sorts_and_limits(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        store.upsert_session(SessionRecord(session_id="old", user_id="u", updated_at="2000-01-01T00:00:00+00:00"))
        store.upsert_session(SessionRecord(session_id="other", user_id="v"))
        store.upsert_session(SessionRecord(session_id="new", user_id="u"))

        sessions = store.list_sessions(user_id="u", limit=1)
        assert [record.session_id for record in sessions] == ["new"]
        with pytest.raises(ValueError, match="non-negative"):
            store.list_sessions(limit=-1)

    def test_delete_session_uses_exact_user_identity(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        store.upsert_session(SessionRecord(session_id="shared", user_id="alice"))
        store.upsert_session(SessionRecord(session_id="shared", user_id="bob"))

        assert store.delete_session("shared", user_id="alice") is True
        assert store.delete_session("shared", user_id="alice") is False
        assert store.get_session("shared", user_id="bob") is not None

    def test_recovers_abandoned_stale_lock(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path, lock_timeout_seconds=0.1, stale_lock_seconds=0.1)
        store.db_path.mkdir(parents=True, exist_ok=True)
        store.lock_path.write_text(json.dumps({"pid": 99999999, "token": "old"}), encoding="ascii")
        old = time.time() - 10
        os.utime(store.lock_path, (old, old))

        store.upsert_session(SessionRecord(session_id="recovered"))
        assert store.get_session("recovered") is not None

    def test_rejects_unsafe_session_table(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid"):
            JsonSessionStore(db_path=tmp_path, session_table="../escape")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_session_file_is_private(self, tmp_path):
        store = JsonSessionStore(db_path=tmp_path)
        store.upsert_session(SessionRecord(session_id="private"))
        assert store.file_path.stat().st_mode & 0o777 == 0o600
        assert store.get_session("s1") is not None
        assert not store.lock_path.exists()


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
