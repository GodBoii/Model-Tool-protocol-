"""Exercise production SQL session stores against explicitly configured databases."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from mtp.session_store import (
    MySQLSessionStore,
    PostgresSessionStore,
    SessionRecord,
    SessionRun,
)

pytestmark = pytest.mark.integration


def test_binary_media_round_trip_on_real_database(store):
    from mtp.media import File, Image

    record = SessionRecord(
        session_id="phase9-media",
        user_id="synthetic-user",
        messages=[
            {
                "role": "user",
                "content": "inspect",
                "images": [Image(content=b"\x89PNG\x00\xff", mime_type="image/png")],
                "files": [File(content=b"%PDF\x00\xff", filename="synthetic.pdf")],
            }
        ],
    )
    store.upsert_session(record)
    loaded = store.get_session(record.session_id, user_id="synthetic-user")
    assert loaded.messages[0]["images"][0].get_content_bytes() == b"\x89PNG\x00\xff"
    assert loaded.messages[0]["files"][0].get_content_bytes() == b"%PDF\x00\xff"


@pytest.fixture(params=["postgres", "mysql"])
def store(request):
    if request.param == "postgres":
        endpoint = os.getenv("MTP_TEST_POSTGRES_URL")
        if not endpoint:
            pytest.skip("Set MTP_TEST_POSTGRES_URL for an isolated test database.")
        pytest.importorskip("psycopg")
        return PostgresSessionStore(
            db_url=endpoint, session_table="mtp_phase9_sessions"
        )
    endpoint = os.getenv("MTP_TEST_MYSQL_HOST")
    if not endpoint:
        pytest.skip("Set MTP_TEST_MYSQL_HOST for an isolated test database.")
    pytest.importorskip("pymysql")
    return MySQLSessionStore(
        host=endpoint,
        port=int(os.getenv("MTP_TEST_MYSQL_PORT", "3306")),
        user=os.environ["MTP_TEST_MYSQL_USER"],
        password=os.environ["MTP_TEST_MYSQL_PASSWORD"],
        database=os.environ["MTP_TEST_MYSQL_DATABASE"],
        session_table="mtp_phase9_sessions",
    )


def test_native_state_unicode_upsert_and_user_scope(store):
    session = SessionRecord(
        session_id="phase9-native-state",
        user_id="synthetic-user",
        metadata={"label": "工具"},
        messages=[
            {
                "role": "assistant",
                "responses_items": [
                    {"type": "reasoning", "encrypted_content": "opaque"}
                ],
                "tool_calls": [
                    {
                        "id": "call_id",
                        "function": {"name": "calculator.add", "arguments": '{"a":17}'},
                    }
                ],
            }
        ],
        runs=[SessionRun(run_id="run-1", input="hello", final_text="done")],
    )
    initial = store.upsert_session(session)
    loaded = store.get_session(session.session_id, user_id="synthetic-user")
    assert loaded.messages == session.messages and loaded.metadata == session.metadata
    assert store.get_session(session.session_id, user_id="wrong-user") is None
    session.metadata["updated"] = True
    later = store.upsert_session(session)
    assert later.created_at == initial.created_at
    assert store.get_session(session.session_id, user_id="synthetic-user").metadata[
        "updated"
    ]


def test_concurrent_independent_sessions_and_literal_sql_input(store):
    records = [
        SessionRecord(
            session_id=f"phase9-concurrent-{index}",
            user_id="synthetic-user",
            metadata={"index": index},
        )
        for index in range(12)
    ]
    with ThreadPoolExecutor(max_workers=4) as workers:
        assert len(list(workers.map(store.upsert_session, records))) == 12
    for record in records:
        assert (
            store.get_session(record.session_id, user_id="synthetic-user").metadata
            == record.metadata
        )
    identifier = "phase9'; DROP TABLE mtp_phase9_sessions; --"
    store.upsert_session(SessionRecord(session_id=identifier, user_id="synthetic-user"))
    assert (
        store.get_session(identifier, user_id="synthetic-user").session_id == identifier
    )
    assert (
        store.get_session(records[0].session_id, user_id="synthetic-user") is not None
    )
