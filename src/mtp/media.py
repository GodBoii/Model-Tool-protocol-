from __future__ import annotations

import base64
from dataclasses import dataclass, field
import ipaddress
from pathlib import Path
import socket
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


DEFAULT_MEDIA_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_MEDIA_TIMEOUT_SECONDS = 20.0


class MediaLoadError(ValueError):
    """Raised when media cannot be loaded safely."""


class MediaTooLargeError(MediaLoadError):
    """Raised before a media source can exceed its configured byte budget."""


class UnsafeMediaURLError(MediaLoadError):
    """Raised when a remote media URL violates the network policy."""


def _ensure_size(payload: bytes, *, max_bytes: int, source: str) -> bytes:
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    if len(payload) > max_bytes:
        raise MediaTooLargeError(
            f"Media source {source!r} exceeds the {max_bytes}-byte limit."
        )
    return payload


def _decode_base64_content(value: str) -> bytes:
    # Reject oversized serialized media before base64 decoding allocates the
    # decoded payload. Whitespace is tolerated for backward compatibility.
    compact_length = sum(not char.isspace() for char in value)
    max_encoded_length = ((DEFAULT_MEDIA_MAX_BYTES + 2) // 3) * 4
    if compact_length > max_encoded_length:
        raise MediaTooLargeError(
            f"Serialized media exceeds the {DEFAULT_MEDIA_MAX_BYTES}-byte limit."
        )
    decoded = base64.b64decode(value)
    return _ensure_size(
        decoded, max_bytes=DEFAULT_MEDIA_MAX_BYTES, source="serialized content"
    )


def _read_bytes_from_source(
    *,
    content: bytes | str | None = None,
    filepath: str | Path | None = None,
    max_bytes: int = DEFAULT_MEDIA_MAX_BYTES,
) -> bytes | None:
    if content is None and filepath is None:
        return None
    if isinstance(content, bytes):
        return _ensure_size(content, max_bytes=max_bytes, source="inline content")
    if isinstance(content, str):
        return _ensure_size(
            content.encode("utf-8"), max_bytes=max_bytes, source="inline content"
        )
    if filepath is not None:
        path = Path(filepath)
        if path.exists() and path.is_file():
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise MediaLoadError(
                    f"Unable to inspect media file {path!s}: {exc}"
                ) from exc
            if size > max_bytes:
                raise MediaTooLargeError(
                    f"Media file {path!s} exceeds the {max_bytes}-byte limit."
                )
            try:
                with path.open("rb") as stream:
                    payload = stream.read(max_bytes + 1)
            except OSError as exc:
                raise MediaLoadError(
                    f"Unable to read media file {path!s}: {exc}"
                ) from exc
            return _ensure_size(payload, max_bytes=max_bytes, source=str(path))
    return None


def _is_disallowed_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    return not parsed.is_global


def validate_remote_media_url(
    url: str,
    *,
    allow_private_network: bool = False,
) -> str:
    """Validate an HTTP(S) media URL before the SDK connects to it.

    DNS must resolve exclusively to globally routable addresses unless the caller
    deliberately opts into private-network access. The check is repeated for
    redirects by :func:`fetch_url_bytes`.
    """

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeMediaURLError(f"Invalid remote media URL: {exc}") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeMediaURLError("Remote media URLs must use http or https.")
    if not parsed.hostname:
        raise UnsafeMediaURLError("Remote media URL must include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeMediaURLError("Credentials are not allowed in remote media URLs.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        if not allow_private_network:
            raise UnsafeMediaURLError("Localhost media URLs are blocked by default.")
    if allow_private_network:
        return url

    try:
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        records = socket.getaddrinfo(
            hostname, port or default_port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise UnsafeMediaURLError(
            f"Remote media hostname {hostname!r} could not be resolved safely."
        ) from exc
    addresses = {str(record[4][0]) for record in records if record[4]}
    if not addresses or any(_is_disallowed_address(address) for address in addresses):
        raise UnsafeMediaURLError(
            f"Remote media hostname {hostname!r} resolves to a non-public address."
        )
    return url


class _SafeMediaRedirectHandler(HTTPRedirectHandler):
    def __init__(self, *, allow_private_network: bool) -> None:
        super().__init__()
        self._allow_private_network = allow_private_network

    def redirect_request(  # type: ignore[override]
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        validate_remote_media_url(
            newurl, allow_private_network=self._allow_private_network
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_remote_media(
    request: Request, *, timeout: float, allow_private_network: bool
) -> Any:
    opener = build_opener(
        _SafeMediaRedirectHandler(allow_private_network=allow_private_network)
    )
    return opener.open(request, timeout=timeout)


def fetch_url_bytes(
    url: str,
    *,
    max_bytes: int = DEFAULT_MEDIA_MAX_BYTES,
    timeout: float = DEFAULT_MEDIA_TIMEOUT_SECONDS,
    allow_private_network: bool = False,
) -> bytes:
    """Fetch remote media with SSRF, timeout, redirect, and size protections."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    validate_remote_media_url(url, allow_private_network=allow_private_network)
    request = Request(url, headers={"User-Agent": "MTP-SDK/0.1"})
    try:
        response_context = _open_remote_media(
            request,
            timeout=timeout,
            allow_private_network=allow_private_network,
        )
        with response_context as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except (TypeError, ValueError) as exc:
                    raise MediaLoadError(
                        "Remote media returned an invalid Content-Length."
                    ) from exc
                if declared_size < 0:
                    raise MediaLoadError(
                        "Remote media returned an invalid Content-Length."
                    )
                if declared_size > max_bytes:
                    raise MediaTooLargeError(
                        f"Remote media exceeds the {max_bytes}-byte limit."
                    )

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(64 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise MediaTooLargeError(
                        f"Remote media exceeds the {max_bytes}-byte limit."
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except MediaLoadError:
        raise
    except (HTTPError, URLError, OSError) as exc:
        raise MediaLoadError(f"Unable to load remote media: {exc}") from exc


@dataclass(slots=True)
class Image:
    url: str | None = None
    filepath: str | Path | None = None
    content: bytes | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    format: str | None = None
    mime_type: str | None = None
    detail: str | None = None
    alt_text: str | None = None

    def get_content_bytes(
        self, *, max_bytes: int = DEFAULT_MEDIA_MAX_BYTES
    ) -> bytes | None:
        return _read_bytes_from_source(
            content=self.content, filepath=self.filepath, max_bytes=max_bytes
        )

    def to_base64(self) -> str | None:
        raw = self.get_content_bytes()
        if raw is None:
            return None
        return base64.b64encode(raw).decode("utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Image":
        content = data.get("content")
        if isinstance(content, str):
            try:
                content = _decode_base64_content(content)
            except MediaTooLargeError:
                raise
            except Exception:
                content = content.encode("utf-8")
                _ensure_size(
                    content,
                    max_bytes=DEFAULT_MEDIA_MAX_BYTES,
                    source="serialized content",
                )
        return cls(
            url=data.get("url"),
            filepath=data.get("filepath"),
            content=content if isinstance(content, bytes) else None,
            id=str(data.get("id") or uuid4()),
            format=data.get("format"),
            mime_type=data.get("mime_type"),
            detail=data.get("detail"),
            alt_text=data.get("alt_text"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "url": self.url,
            "filepath": str(self.filepath) if self.filepath is not None else None,
            "format": self.format,
            "mime_type": self.mime_type,
            "detail": self.detail,
            "alt_text": self.alt_text,
        }
        encoded = self.to_base64()
        if encoded is not None:
            payload["content"] = encoded
        return {k: v for k, v in payload.items() if v is not None}


@dataclass(slots=True)
class Audio:
    url: str | None = None
    filepath: str | Path | None = None
    content: bytes | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    format: str | None = None
    mime_type: str | None = None
    transcript: str | None = None

    def get_content_bytes(
        self, *, max_bytes: int = DEFAULT_MEDIA_MAX_BYTES
    ) -> bytes | None:
        return _read_bytes_from_source(
            content=self.content, filepath=self.filepath, max_bytes=max_bytes
        )

    def to_base64(self) -> str | None:
        raw = self.get_content_bytes()
        if raw is None:
            return None
        return base64.b64encode(raw).decode("utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Audio":
        content = data.get("content")
        if isinstance(content, str):
            try:
                content = _decode_base64_content(content)
            except MediaTooLargeError:
                raise
            except Exception:
                content = content.encode("utf-8")
                _ensure_size(
                    content,
                    max_bytes=DEFAULT_MEDIA_MAX_BYTES,
                    source="serialized content",
                )
        return cls(
            url=data.get("url"),
            filepath=data.get("filepath"),
            content=content if isinstance(content, bytes) else None,
            id=str(data.get("id") or uuid4()),
            format=data.get("format"),
            mime_type=data.get("mime_type"),
            transcript=data.get("transcript"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "url": self.url,
            "filepath": str(self.filepath) if self.filepath is not None else None,
            "format": self.format,
            "mime_type": self.mime_type,
            "transcript": self.transcript,
        }
        encoded = self.to_base64()
        if encoded is not None:
            payload["content"] = encoded
        return {k: v for k, v in payload.items() if v is not None}


@dataclass(slots=True)
class Video:
    url: str | None = None
    filepath: str | Path | None = None
    content: bytes | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    format: str | None = None
    mime_type: str | None = None

    def get_content_bytes(
        self, *, max_bytes: int = DEFAULT_MEDIA_MAX_BYTES
    ) -> bytes | None:
        return _read_bytes_from_source(
            content=self.content, filepath=self.filepath, max_bytes=max_bytes
        )

    def to_base64(self) -> str | None:
        raw = self.get_content_bytes()
        if raw is None:
            return None
        return base64.b64encode(raw).decode("utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Video":
        content = data.get("content")
        if isinstance(content, str):
            try:
                content = _decode_base64_content(content)
            except MediaTooLargeError:
                raise
            except Exception:
                content = content.encode("utf-8")
                _ensure_size(
                    content,
                    max_bytes=DEFAULT_MEDIA_MAX_BYTES,
                    source="serialized content",
                )
        return cls(
            url=data.get("url"),
            filepath=data.get("filepath"),
            content=content if isinstance(content, bytes) else None,
            id=str(data.get("id") or uuid4()),
            format=data.get("format"),
            mime_type=data.get("mime_type"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "url": self.url,
            "filepath": str(self.filepath) if self.filepath is not None else None,
            "format": self.format,
            "mime_type": self.mime_type,
        }
        encoded = self.to_base64()
        if encoded is not None:
            payload["content"] = encoded
        return {k: v for k, v in payload.items() if v is not None}


@dataclass(slots=True)
class File:
    id: str | None = None
    url: str | None = None
    filepath: str | Path | None = None
    content: bytes | str | None = None
    mime_type: str | None = None
    filename: str | None = None
    format: str | None = None

    def get_content_bytes(
        self, *, max_bytes: int = DEFAULT_MEDIA_MAX_BYTES
    ) -> bytes | None:
        return _read_bytes_from_source(
            content=self.content, filepath=self.filepath, max_bytes=max_bytes
        )

    def to_base64(self) -> str | None:
        raw = self.get_content_bytes()
        if raw is None:
            return None
        return base64.b64encode(raw).decode("utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "File":
        content = data.get("content")
        if isinstance(content, str):
            try:
                content = _decode_base64_content(content)
            except MediaTooLargeError:
                raise
            except Exception:
                _ensure_size(
                    content.encode("utf-8"),
                    max_bytes=DEFAULT_MEDIA_MAX_BYTES,
                    source="serialized content",
                )
        return cls(
            id=data.get("id"),
            url=data.get("url"),
            filepath=data.get("filepath"),
            content=content if isinstance(content, (bytes, str)) else None,
            mime_type=data.get("mime_type"),
            filename=data.get("filename"),
            format=data.get("format"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "url": self.url,
            "filepath": str(self.filepath) if self.filepath is not None else None,
            "mime_type": self.mime_type,
            "filename": self.filename,
            "format": self.format,
        }
        if isinstance(self.content, str):
            payload["content"] = self.content
        else:
            encoded = self.to_base64()
            if encoded is not None:
                payload["content"] = encoded
        return {k: v for k, v in payload.items() if v is not None}


def _coerce_media_list(
    values: Iterable[Any] | None,
    *,
    ctor: Any,
) -> list[Any] | None:
    if values is None:
        return None
    out: list[Any] = []
    for item in values:
        if isinstance(item, ctor):
            out.append(item)
        elif isinstance(item, dict):
            out.append(ctor.from_dict(item))
    return out or None


def coerce_images(values: Iterable[Any] | None) -> list[Image] | None:
    return _coerce_media_list(values, ctor=Image)


def coerce_audios(values: Iterable[Any] | None) -> list[Audio] | None:
    return _coerce_media_list(values, ctor=Audio)


def coerce_videos(values: Iterable[Any] | None) -> list[Video] | None:
    return _coerce_media_list(values, ctor=Video)


def coerce_files(values: Iterable[Any] | None) -> list[File] | None:
    return _coerce_media_list(values, ctor=File)
