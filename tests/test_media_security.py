from __future__ import annotations

from io import BytesIO
import socket
from typing import Any

import pytest

from mtp.media import (
    Audio,
    File,
    Image,
    MediaTooLargeError,
    UnsafeMediaURLError,
    fetch_url_bytes,
    validate_remote_media_url,
)
from mtp.providers.common import _fetch_url_bytes, _image_to_openai_part


def _resolve_to(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    records = [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, 443),
        )
        for address in addresses
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: records)


class _Response:
    def __init__(self, payload: bytes, *, content_length: str | None = None) -> None:
        self._stream = BytesIO(payload)
        self.headers = {} if content_length is None else {"Content-Length": content_length}
        self.read_sizes: list[int] = []

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._stream.read(size)


def test_local_and_inline_media_reads_are_bounded(tmp_path: Any) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"12345")

    assert File(filepath=path).get_content_bytes(max_bytes=5) == b"12345"
    with pytest.raises(MediaTooLargeError, match="4-byte limit"):
        File(filepath=path).get_content_bytes(max_bytes=4)
    with pytest.raises(MediaTooLargeError, match="inline content"):
        Audio(content=b"12345").get_content_bytes(max_bytes=4)


def test_deserialized_media_is_bounded_before_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtp import media

    monkeypatch.setattr(media, "DEFAULT_MEDIA_MAX_BYTES", 4)
    with pytest.raises(MediaTooLargeError, match="4-byte limit"):
        Image.from_dict({"content": "MTIzNDU="})


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/payload",
        "data:audio/wav;base64,AAAA",
        "http://user:secret@example.com/media",
    ],
)
def test_remote_loader_enforces_scheme_and_credential_policy(url: str) -> None:
    with pytest.raises(UnsafeMediaURLError):
        validate_remote_media_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/media",
        "http://service.localhost/media",
        "http://127.0.0.1/media",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/media",
    ],
)
def test_remote_loader_blocks_local_and_link_local_targets(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    hostname = url.split("//", 1)[1].split("/", 1)[0].strip("[]")
    if hostname.endswith("localhost"):
        # Localhost is rejected without consulting DNS.
        monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [])
    elif hostname == "127.0.0.1":
        _resolve_to(monkeypatch, "127.0.0.1")
    elif hostname == "169.254.169.254":
        _resolve_to(monkeypatch, "169.254.169.254")
    else:
        _resolve_to(monkeypatch, "::1")

    with pytest.raises(UnsafeMediaURLError):
        validate_remote_media_url(url)


def test_remote_loader_rejects_mixed_public_and_private_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolve_to(monkeypatch, "93.184.216.34", "10.0.0.7")

    with pytest.raises(UnsafeMediaURLError, match="non-public"):
        validate_remote_media_url("https://media.example.test/file")


def test_private_network_access_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolve_to(monkeypatch, "10.0.0.7")

    assert validate_remote_media_url(
        "http://10.0.0.7/file", allow_private_network=True
    ) == "http://10.0.0.7/file"


def test_redirect_handler_revalidates_each_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtp.media import _SafeMediaRedirectHandler

    _resolve_to(monkeypatch, "127.0.0.1")
    handler = _SafeMediaRedirectHandler(allow_private_network=False)

    with pytest.raises(UnsafeMediaURLError):
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "http://internal.example.test/secret",
        )


def test_remote_loader_rejects_declared_oversize_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtp import media

    _resolve_to(monkeypatch, "93.184.216.34")
    response = _Response(b"payload", content_length="100")
    monkeypatch.setattr(media, "_open_remote_media", lambda *args, **kwargs: response)

    with pytest.raises(MediaTooLargeError, match="8-byte limit"):
        fetch_url_bytes("https://example.com/file", max_bytes=8)
    assert response.read_sizes == []


def test_remote_loader_caps_stream_without_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtp import media

    _resolve_to(monkeypatch, "93.184.216.34")
    response = _Response(b"123456789")
    monkeypatch.setattr(media, "_open_remote_media", lambda *args, **kwargs: response)

    with pytest.raises(MediaTooLargeError, match="8-byte limit"):
        fetch_url_bytes("https://example.com/file", max_bytes=8)
    assert max(response.read_sizes) <= 9


def test_remote_loader_returns_bounded_public_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mtp import media

    _resolve_to(monkeypatch, "93.184.216.34")
    response = _Response(b"payload", content_length="7")
    monkeypatch.setattr(media, "_open_remote_media", lambda *args, **kwargs: response)

    assert fetch_url_bytes("https://example.com/file", max_bytes=8) == b"payload"


def test_provider_fetch_fails_closed_without_exposing_security_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolve_to(monkeypatch, "127.0.0.1")
    assert _fetch_url_bytes("http://127.0.0.1/private") is None


def test_provider_image_and_data_urls_remain_pass_through() -> None:
    public_url = "https://cdn.example.test/image.png"
    data_url = "data:image/png;base64,iVBORw0KGgo="

    assert _image_to_openai_part(Image(url=public_url)) == {
        "type": "image_url",
        "image_url": {"url": public_url},
    }
    assert _image_to_openai_part(Image(url=data_url)) == {
        "type": "image_url",
        "image_url": {"url": data_url},
    }
