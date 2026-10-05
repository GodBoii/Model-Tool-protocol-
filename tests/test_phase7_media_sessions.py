"""Persisted media payloads retain exact bytes and native Responses inputs."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace as NS

import pytest

from mtp.media import Audio, File, Image, Video
from mtp.providers import OpenAIResponses
from mtp.session_store import JsonSessionStore, SessionRecord, _json_safe


@pytest.mark.parametrize(
    "field,media",
    [
        ("images", Image(content=b"\x89PNG\x00\xff\x80", mime_type="image/png")),
        ("audios", Audio(content=b"\xff\x00audio", format="wav")),
        ("videos", Video(content=b"\xff\x00video", format="mp4")),
        ("files", File(content=b"\xff\x00file", filename="binary.bin")),
        ("files", File(content="test", filename="plain.txt")),
        ("files", File(content="Unicode snowman \u2603", filename="unicode.txt")),
    ],
)
def test_media_binary_and_text_round_trip_through_json_and_sql_record_contract(
    tmp_path, field, media
):
    expected = media.get_content_bytes()
    record = SessionRecord(
        session_id="media",
        messages=[{"role": "user", "content": "inspect", field: [media]}],
    )
    serialized = record.to_dict()
    # PostgreSQL and MySQL store these same record fields as JSON columns.
    for _ in range(2):
        loaded = SessionRecord.from_dict(json.loads(json.dumps(serialized)))
        assert loaded.messages[0][field][0].get_content_bytes() == expected
        serialized = loaded.to_dict()
    store = JsonSessionStore(db_path=tmp_path)
    store.upsert_session(record)
    assert (
        store.get_session("media").messages[0][field][0].get_content_bytes() == expected
    )
    assert base64.b64decode(serialized["messages"][0][field][0]["content"]) == expected


def test_responses_replays_media_from_saved_session_without_original_files(tmp_path):
    image_path = tmp_path / "image.png"
    file_path = tmp_path / "document.pdf"
    image_path.write_bytes(b"\x89PNG\x00\xff")
    file_path.write_bytes(b"%PDF\x00\xff")
    store = JsonSessionStore(db_path=tmp_path / "sessions")
    store.upsert_session(
        SessionRecord(
            session_id="s",
            messages=[
                {
                    "role": "user",
                    "content": "inspect",
                    "images": [Image(filepath=image_path)],
                    "files": [File(filepath=file_path)],
                }
            ],
        )
    )
    image_path.unlink()
    file_path.unlink()
    provider = OpenAIResponses(
        client=NS(), enable_multimodal=True, input_modalities=("text", "image", "file")
    )
    content = provider._request(store.get_session("s").messages, [])["input"][0][
        "content"
    ]
    assert (
        base64.b64decode(content[1]["image_url"].split(",", 1)[1]) == b"\x89PNG\x00\xff"
    )
    assert base64.b64decode(content[2]["file_data"].split(",", 1)[1]) == b"%PDF\x00\xff"
    assert content[2]["filename"] == "document.pdf"


def test_non_media_json_safe_bytes_keep_existing_behavior():
    assert _json_safe(b"hello") == "hello"
    assert _json_safe({"payload": b"hello"}) == {"payload": "hello"}
