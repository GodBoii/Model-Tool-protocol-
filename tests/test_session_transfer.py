from __future__ import annotations

import json

import pytest

from mtp.cli.main import main
from mtp.session_store import JsonSessionStore, SessionRecord, SessionRun
from mtp.session_transfer import (
    SessionTransferError,
    make_session_export,
    parse_session_export,
    write_session_export,
)


def _record(session_id: str, user_id: str | None = None, content: str = "hello") -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        user_id=user_id,
        metadata={"project": "demo"},
        messages=[{"role": "user", "content": content}],
        runs=[SessionRun(run_id="run-1", input=content, final_text="done")],
    )


def test_transfer_round_trip_and_schema_validation() -> None:
    payload = make_session_export([_record("one", "alice")])
    restored = parse_session_export(payload)
    assert restored[0].session_id == "one"
    assert restored[0].runs[0].final_text == "done"

    payload["sessions"][0]["unexpected"] = True
    with pytest.raises(SessionTransferError, match="unknown field"):
        parse_session_export(payload)


def test_transfer_rejects_duplicate_identity_and_bad_types() -> None:
    payload = make_session_export([_record("one", "alice"), _record("one", "alice")])
    with pytest.raises(SessionTransferError, match="duplicates"):
        parse_session_export(payload)

    payload = make_session_export([_record("one")])
    payload["sessions"][0]["runs"][0]["total_tool_calls"] = True
    with pytest.raises(SessionTransferError, match="non-negative integer"):
        parse_session_export(payload)


def test_atomic_export_refuses_overwrite_by_default(tmp_path) -> None:
    target = tmp_path / "sessions.json"
    first = make_session_export([_record("one")])
    second = make_session_export([_record("two")])
    write_session_export(target, first)
    original = target.read_bytes()

    with pytest.raises(FileExistsError):
        write_session_export(target, second)
    assert target.read_bytes() == original

    write_session_export(target, second, overwrite=True)
    assert json.loads(target.read_text(encoding="utf-8"))["sessions"][0]["session_id"] == "two"
    assert not list(tmp_path.glob(".*.tmp"))


def test_store_import_is_all_or_nothing_and_preserves_other_users(tmp_path) -> None:
    store = JsonSessionStore(db_path=tmp_path)
    store.upsert_session(_record("shared", "alice", "old"))
    store.upsert_session(_record("shared", "bob", "bob"))

    with pytest.raises(FileExistsError):
        store.import_sessions([_record("new", "alice"), _record("shared", "alice", "replacement")])
    assert store.get_session("new", user_id="alice") is None
    assert store.get_session("shared", user_id="alice").messages[0]["content"] == "old"

    assert store.import_sessions([_record("shared", "alice", "replacement")], overwrite=True) == 1
    assert store.get_session("shared", user_id="alice").messages[0]["content"] == "replacement"
    assert store.get_session("shared", user_id="bob").messages[0]["content"] == "bob"


def test_cli_show_export_import_and_user_filter(tmp_path, capsys) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    export_path = tmp_path / "bundle.json"
    store = JsonSessionStore(db_path=source)
    store.upsert_session(_record("shared", "alice", "alice"))
    store.upsert_session(_record("shared", "bob", "bob"))

    assert main(["sessions", "show", "shared", "--session-db", str(source)]) == 2
    capsys.readouterr()
    assert main([
        "sessions", "show", "shared", "--session-db", str(source), "--user-id", "alice", "--json"
    ]) == 0
    assert json.loads(capsys.readouterr().out)["user_id"] == "alice"

    assert main([
        "sessions", "export", str(export_path), "--session-db", str(source), "--user-id", "alice"
    ]) == 0
    capsys.readouterr()
    assert main([
        "sessions", "import", str(export_path), "--session-db", str(destination), "--user-id", "bob"
    ]) == 1
    capsys.readouterr()
    assert main([
        "sessions", "import", str(export_path), "--session-db", str(destination), "--user-id", "alice"
    ]) == 0
    imported = JsonSessionStore(db_path=destination)
    assert imported.get_session("shared", user_id="alice") is not None
    assert imported.get_session("shared", user_id="bob") is None


def test_cli_import_rejects_malformed_bundle_without_partial_write(tmp_path, capsys) -> None:
    bundle = tmp_path / "bad.json"
    payload = make_session_export([_record("ok"), _record("bad")])
    payload["sessions"][1]["messages"] = "not-a-list"
    bundle.write_text(json.dumps(payload), encoding="utf-8")
    destination = tmp_path / "db"

    assert main(["sessions", "import", str(bundle), "--session-db", str(destination)]) == 1
    assert JsonSessionStore(db_path=destination).list_sessions() == []
    assert "must be an array" in capsys.readouterr().err
