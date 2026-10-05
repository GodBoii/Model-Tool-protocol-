"""Opt-in, stateless MCP 2026-07-28 Streamable HTTP with JSON responses."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import math
import queue
import re
import select
import socket
import threading
import time
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from typing import Any
from urllib.parse import urlsplit

from .mcp import MCPJsonRpcServer
from .mcp_features import MCPOAuthMetadata

PROTOCOL_VERSION = "2026-07-28"
_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
_FIELD_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_SAFE_INTEGER = 2**53 - 1
_LOG = logging.getLogger(__name__)


class _HTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer resolves getfqdn here even for numeric loopback addresses.
        # This transport needs the bound address, never a reverse DNS name.
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def _error(request: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
    }
    if isinstance(request, dict) and "id" in request:
        request_id = request["id"]
        if isinstance(request_id, str) or type(request_id) is int:
            result["id"] = request_id
    if data is not None:
        result["error"]["data"] = data
    return result


def _decode_header(value: str) -> str:
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError("Malformed Base64 header value") from exc
    if value != value.strip() or any(
        ord(c) > 126 or (ord(c) < 32 and c != "\t") for c in value
    ):
        raise ValueError("Header value requires Base64 encoding")
    return value


def _header_parameters(
    schema: dict[str, Any],
) -> list[tuple[str, tuple[str, ...], str]]:
    """Validate every annotation, including annotations in unreachable schema branches."""
    found: list[tuple[str, tuple[str, ...], str]] = []
    used: set[str] = set()

    def visit(node: Any, path: tuple[str, ...], reachable: bool) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child, path, False)
            return
        if not isinstance(node, dict):
            return
        if "x-mcp-header" in node:
            name = node["x-mcp-header"]
            kind = node.get("type")
            if not reachable or not path:
                raise ValueError(
                    "x-mcp-header must be reachable through properties only"
                )
            if not isinstance(name, str) or not _FIELD_TOKEN.fullmatch(name):
                raise ValueError("x-mcp-header must be a nonempty HTTP field token")
            if not isinstance(kind, str) or kind not in {
                "string",
                "integer",
                "boolean",
            }:
                raise ValueError(
                    "x-mcp-header requires string, integer, or boolean type"
                )
            if name.lower() in used:
                raise ValueError("x-mcp-header names must be unique ignoring case")
            used.add(name.lower())
            found.append((name, path, kind))
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for name, child in value.items():
                    visit(child, (*path, name), reachable)
            else:
                visit(value, path, False)

    visit(schema, (), True)
    return found


class MCPStreamableHTTPTransportServer:
    """A separate POST /mcp transport. No legacy sessions or global event streams.

    ``start`` blocks like MCPHTTPTransportServer.start. Run it in a thread when
    embedding, and call ``shutdown`` from another thread. Authentication is
    delegated to MCPJsonRpcServer using only the HTTP Bearer credential.
    """

    def __init__(
        self,
        host: str,
        port: int,
        server: MCPJsonRpcServer,
        *,
        allowed_origins: set[str] | None = None,
        max_body_bytes: int = 1_048_576,
        read_timeout_seconds: float = 10.0,
        sse_response: bool = False,
        oauth_metadata: MCPOAuthMetadata | None = None,
        max_streams: int = 128,
        max_stream_seconds: float = 3600,
    ) -> None:
        if not getattr(server, "modern_enabled", False):
            raise ValueError(
                "Streamable HTTP requires MCPJsonRpcServer(enable_modern=True)"
            )
        if type(max_body_bytes) is not int or max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be a positive integer")
        if not math.isfinite(read_timeout_seconds) or read_timeout_seconds <= 0:
            raise ValueError("read_timeout_seconds must be finite and positive")
        self.host = host
        self.port = port
        self.server = server
        self.allowed_origins = (
            set(allowed_origins) if allowed_origins is not None else None
        )
        self.max_body_bytes = max_body_bytes
        self.read_timeout_seconds = read_timeout_seconds
        if (
            type(sse_response) is not bool
            or type(max_streams) is not int
            or max_streams <= 0
        ):
            raise ValueError(
                "SSE flag must be boolean and max_streams positive integer"
            )
        if not math.isfinite(max_stream_seconds) or max_stream_seconds <= 0:
            raise ValueError("max_stream_seconds must be finite and positive")
        if oauth_metadata is not None and not server._requires_auth("tools/call"):
            raise ValueError("OAuth metadata requires a configured token authorizer")
        self.sse_response = sse_response
        self.oauth_metadata = oauth_metadata
        self.max_streams = max_streams
        self.max_stream_seconds = max_stream_seconds
        self._stream_count = 0
        self._stream_lock = threading.Lock()
        self._stopping = threading.Event()
        self._http: ThreadingHTTPServer | None = None

    @property
    def address(self) -> tuple[str, int] | None:
        if self._http is None:
            return None
        host, port = self._http.server_address[:2]
        return str(host), int(port)

    def _validate_headers(self, headers: Any, request: dict[str, Any]) -> str | None:
        params = request.get("params")
        params = params if isinstance(params, dict) else {}
        meta = params.get("_meta")
        meta = meta if isinstance(meta, dict) else {}

        def check(name: str, expected: Any, *, encoded: bool = False) -> None:
            values = headers.get_all(name, [])
            if len(values) != 1:
                raise ValueError(f"Missing or duplicate {name} header")
            value = _decode_header(values[0]) if encoded else values[0]
            if not encoded and any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise ValueError(f"Malformed {name} header")
            if not isinstance(expected, str) or value != expected:
                raise ValueError(f"{name} does not match request body")

        try:
            check("MCP-Protocol-Version", meta.get(_VERSION_KEY))
            check("Mcp-Method", request.get("method"))
            method = request.get("method")
            if method in {"tools/call", "prompts/get", "resources/read"}:
                field = "uri" if method == "resources/read" else "name"
                check("Mcp-Name", params.get(field), encoded=True)
            if method == "tools/call":
                spec = next(
                    (
                        item
                        for item in self.server.tools.list_tools()
                        if item.name == params.get("name")
                    ),
                    None,
                )
                if spec is not None:
                    arguments = params.get("arguments", {})
                    for name, path, kind in _header_parameters(spec.input_schema):
                        value: Any = arguments
                        for segment in path:
                            value = (
                                value.get(segment) if isinstance(value, dict) else None
                            )
                        raw_headers = headers.get_all(f"Mcp-Param-{name}", [])
                        if value is None:
                            if raw_headers:
                                raise ValueError(f"Unexpected Mcp-Param-{name} header")
                            continue
                        if len(raw_headers) != 1:
                            raise ValueError(
                                f"Missing or duplicate Mcp-Param-{name} header"
                            )
                        decoded = _decode_header(raw_headers[0])
                        if kind == "string":
                            matches = isinstance(value, str) and decoded == value
                        elif kind == "boolean":
                            matches = (
                                type(value) is bool and decoded == str(value).lower()
                            )
                        else:
                            if (
                                type(value) not in (int, float)
                                or abs(value) > _SAFE_INTEGER
                                or not math.isfinite(value)
                                or value != int(value)
                            ):
                                raise ValueError(
                                    "Mirrored integer is outside the safe integer range"
                                )
                            try:
                                numeric = Decimal(decoded)
                                matches = numeric.is_finite() and numeric == value
                            except InvalidOperation:
                                matches = False
                        if not matches:
                            raise ValueError(
                                f"Mcp-Param-{name} does not match request body"
                            )
        except ValueError as exc:
            return str(exc)
        return None

    def _sanitize_auth(self, request: dict[str, Any], headers: Any) -> dict[str, Any]:
        request = dict(request)
        request.pop("meta", None)
        params = dict(request.get("params") or {})
        params.pop("auth_token", None)
        meta = params.get("_meta")
        if isinstance(meta, dict):
            meta = dict(meta)
            meta.pop("authToken", None)
            meta.pop("auth_token", None)
            params["_meta"] = meta
        authorization = headers.get_all("Authorization", [])
        if len(authorization) == 1:
            scheme, _, credential = authorization[0].partition(" ")
            if (
                scheme.lower() == "bearer"
                and credential
                and credential == credential.strip()
            ):
                params["auth_token"] = credential
        request["params"] = params
        return request

    def _filter_tool_definitions(self, response: dict[str, Any]) -> None:
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return
        valid = []
        for tool in result["tools"]:
            try:
                _header_parameters(tool.get("inputSchema", {}))
            except ValueError as exc:
                _LOG.warning(
                    "Excluding tool %s from Streamable HTTP: %s", tool.get("name"), exc
                )
            else:
                valid.append(tool)
        result["tools"] = valid

    def start(self) -> None:
        if self._http is not None:
            raise RuntimeError("Transport already started")
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(outer.read_timeout_seconds)

            def _write(
                self, status: int, payload: Any = None, *, allow: bool = False
            ) -> None:
                body = (
                    json.dumps(payload, allow_nan=False).encode("utf-8")
                    if payload is not None
                    else b""
                )
                self.send_response(status)
                if payload is not None:
                    self.send_header("Content-Type", "application/json")
                if allow:
                    self.send_header("Allow", "POST")
                if isinstance(payload, dict):
                    data = payload.get("error", {}).get("data", {})
                    challenge = (
                        data.get("www_authenticate") if isinstance(data, dict) else None
                    )
                    if (
                        isinstance(challenge, str)
                        and challenge
                        and "\r" not in challenge
                        and "\n" not in challenge
                    ):
                        if (
                            outer.oauth_metadata is not None
                            and status in {401, 403}
                            and "resource_metadata=" not in challenge
                        ):
                            challenge += f', resource_metadata="{outer.oauth_metadata.metadata_url}"'
                        self.send_header("WWW-Authenticate", challenge)
                    elif outer.oauth_metadata is not None and status == 401:
                        self.send_header(
                            "WWW-Authenticate",
                            f'Bearer resource_metadata="{outer.oauth_metadata.metadata_url}"',
                        )
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    if self.command != "HEAD":
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _origin(self) -> bool:
                origins = self.headers.get_all("Origin", [])
                if not origins:
                    return True
                assert outer.address is not None
                port = outer.address[1]
                allowed = (
                    outer.allowed_origins
                    if outer.allowed_origins is not None
                    else {
                        f"http://localhost:{port}",
                        f"http://127.0.0.1:{port}",
                        f"http://[::1]:{port}",
                    }
                )
                if len(origins) == 1 and origins[0] in allowed:
                    return True
                self._write(403, _error(None, -32600, "Untrusted Origin"))
                return False

            def _unsupported(self) -> None:
                if self._origin():
                    if self.command == "GET" and outer.oauth_metadata is not None:
                        path = urlsplit(self.path).path
                        if path in {
                            outer.oauth_metadata.metadata_path,
                            "/.well-known/oauth-protected-resource",
                        }:
                            self._write(200, outer.oauth_metadata.protected_resource())
                            return
                        if (
                            path == "/.well-known/oauth-authorization-server"
                            and outer.oauth_metadata.authorization_server_metadata
                            is not None
                        ):
                            self._write(
                                200, outer.oauth_metadata.authorization_server_metadata
                            )
                            return
                    self._write(
                        405 if urlsplit(self.path).path == "/mcp" else 404, allow=True
                    )

            do_GET = _unsupported
            do_DELETE = _unsupported
            do_PUT = _unsupported
            do_PATCH = _unsupported
            do_OPTIONS = _unsupported
            do_HEAD = _unsupported

            def _disconnected(self) -> bool:
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    return (
                        bool(readable)
                        and self.connection.recv(1, socket.MSG_PEEK) == b""
                    )
                except (OSError, ValueError):
                    return True

            def _sse_frame(self, payload: dict[str, Any] | None = None) -> None:
                raw = (
                    b": keepalive\n\n"
                    if payload is None
                    else (
                        "data: " + json.dumps(payload, allow_nan=False) + "\n\n"
                    ).encode()
                )
                self.wfile.write(raw)
                self.wfile.flush()

            async def _stream(self, request: dict[str, Any]) -> None:
                modern = outer.server._modern_requests
                issue = modern.validate(request)
                if issue is not None:
                    self._write(404 if issue["error"]["code"] == -32601 else 400, issue)
                    return
                if outer.server._requires_auth(request["method"]):
                    decision = await outer.server._authorized_async(
                        request,
                        method=request["method"],
                        request_id=request["id"],
                        params=request["params"],
                    )
                    if not decision.allowed:
                        failure = modern._auth_failure(request["id"], decision)
                        self._write(failure["error"]["data"]["http_status"], failure)
                        return
                with outer._stream_lock:
                    if outer._stream_count >= outer.max_streams:
                        self._write(
                            503, _error(request, 1002, "Stream capacity reached")
                        )
                        return
                    outer._stream_count += 1
                events: queue.Queue[dict[str, Any]] = queue.Queue(64)

                def notify(event: dict[str, Any]) -> None:
                    try:
                        events.put_nowait(event)
                    except queue.Full as exc:
                        raise ValueError("Request notification queue is full") from exc

                task = None
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-Accel-Buffering", "no")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    self._sse_frame()
                    task = asyncio.create_task(
                        modern.handle(request, notify=notify, _authorized=True)
                    )
                    started = time.monotonic()
                    heartbeat = started
                    while True:
                        if self._disconnected():
                            return
                        if (
                            outer._stopping.is_set()
                            and request["method"] != "subscriptions/listen"
                        ) or time.monotonic() - started > outer.max_stream_seconds:
                            task.cancel()
                            return
                        while not events.empty():
                            self._sse_frame(events.get_nowait())
                        if task.done():
                            response = await task
                            if response is not None:
                                self._sse_frame(response)
                            return
                        if time.monotonic() - heartbeat >= 1:
                            self._sse_frame()
                            heartbeat = time.monotonic()
                        await asyncio.sleep(0.025)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    return
                finally:
                    if task is not None and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    with outer._stream_lock:
                        outer._stream_count -= 1

            def do_POST(self) -> None:
                if not self._origin():
                    return
                if urlsplit(self.path).path != "/mcp":
                    self._write(404)
                    return
                content_types = self.headers.get_all("Content-Type", [])
                if (
                    len(content_types) != 1
                    or content_types[0].split(";", 1)[0].strip().lower()
                    != "application/json"
                ):
                    self._write(
                        415,
                        _error(None, -32600, "Content-Type must be application/json"),
                    )
                    return
                accept = set()
                for value in self.headers.get_all("Accept", []):
                    for item in value.split(","):
                        media_type, *parameters = item.split(";")
                        quality = 1.0
                        for parameter in parameters:
                            name, _, raw_quality = parameter.strip().partition("=")
                            if name.lower() == "q":
                                try:
                                    quality = float(raw_quality)
                                except ValueError:
                                    quality = 0.0
                        if 0 < quality <= 1:
                            accept.add(media_type.strip().lower())
                if not {"application/json", "text/event-stream"}.issubset(accept):
                    self._write(
                        406,
                        _error(
                            None,
                            -32600,
                            "Accept must include application/json and text/event-stream",
                        ),
                    )
                    return
                lengths = self.headers.get_all("Content-Length", [])
                if (
                    self.headers.get("Transfer-Encoding") is not None
                    or len(lengths) != 1
                    or not lengths[0].isascii()
                    or not lengths[0].isdigit()
                ):
                    self._write(
                        400,
                        _error(
                            None,
                            -32600,
                            "A single Content-Length is required; chunked bodies are unsupported",
                        ),
                    )
                    return
                if len(lengths[0]) > 20:
                    self._write(
                        413, _error(None, -32600, "Request body exceeds max_body_bytes")
                    )
                    return
                length = int(lengths[0])
                if length > outer.max_body_bytes:
                    self._write(
                        413, _error(None, -32600, "Request body exceeds max_body_bytes")
                    )
                    return
                try:
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        self._write(
                            400, _error(None, -32700, "Incomplete request body")
                        )
                        return

                    def reject_constant(value: str) -> None:
                        raise ValueError("Nonfinite JSON numbers are unsupported")

                    def finite_float(value: str) -> float:
                        result = float(value)
                        if not math.isfinite(result):
                            raise ValueError("Nonfinite JSON numbers are unsupported")
                        return result

                    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                        result: dict[str, Any] = {}
                        for key, value in pairs:
                            if key in result:
                                raise ValueError("Duplicate JSON member")
                            result[key] = value
                        return result

                    request = json.loads(
                        raw.decode("utf-8"),
                        parse_constant=reject_constant,
                        parse_float=finite_float,
                        object_pairs_hook=unique_object,
                    )
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    ValueError,
                    RecursionError,
                ):
                    self._write(400, _error(None, -32700, "Invalid JSON body"))
                    return
                except TimeoutError:
                    self._write(
                        408, _error(None, -32700, "Request body read timed out")
                    )
                    return
                if (
                    not isinstance(request, dict)
                    or request.get("jsonrpc") != "2.0"
                    or not isinstance(request.get("method"), str)
                    or not request["method"]
                    or "result" in request
                    or "error" in request
                ):
                    self._write(
                        400,
                        _error(request, -32600, "Expected a single JSON-RPC request"),
                    )
                    return
                if "id" not in request:
                    # The modern core defines no HTTP client notifications.
                    self._write(
                        400,
                        _error(
                            None,
                            -32601,
                            "Client notifications are unsupported on this transport",
                        ),
                    )
                    return
                request_id = request["id"]
                if not (isinstance(request_id, str) or type(request_id) is int):
                    self._write(
                        400,
                        _error(None, -32600, "Request id must be a string or integer"),
                    )
                    return
                if not isinstance(request.get("params"), dict):
                    self._write(
                        400, _error(request, -32602, "Request params must be an object")
                    )
                    return
                meta = request["params"].get("_meta")
                if self.headers.get("MCP-Protocol-Version") is not None and (
                    not isinstance(meta, dict)
                    or not isinstance(meta.get(_VERSION_KEY), str)
                ):
                    self._write(
                        400,
                        _error(
                            request,
                            -32602,
                            "Request _meta must include protocolVersion",
                        ),
                    )
                    return
                mismatch = outer._validate_headers(self.headers, request)
                if mismatch is not None:
                    self._write(400, _error(request, -32020, mismatch))
                    return
                if self.headers.get("MCP-Protocol-Version") != PROTOCOL_VERSION:
                    self._write(
                        400,
                        _error(
                            request,
                            -32022,
                            "Unsupported protocol version",
                            {
                                "requested": self.headers.get("MCP-Protocol-Version"),
                                "supported": [PROTOCOL_VERSION],
                            },
                        ),
                    )
                    return
                try:
                    sanitized = outer._sanitize_auth(request, self.headers)
                    if outer.sse_response and (
                        request["method"] == "subscriptions/listen"
                        or "progressToken" in request["params"]["_meta"]
                    ):
                        asyncio.run(self._stream(sanitized))
                        return
                    response = asyncio.run(outer.server.ahandle_request(sanitized))
                    if response is None:
                        self._write(
                            500,
                            _error(request, -32603, "Server did not answer request"),
                        )
                        return
                    if request["method"] == "tools/list":
                        outer._filter_tool_definitions(response)
                    if request["method"] == "server/discover" and isinstance(
                        response.get("result"), dict
                    ):
                        response["result"]["supportedVersions"] = [PROTOCOL_VERSION]
                        if not outer.sse_response:
                            for capability in (
                                response["result"].get("capabilities", {}).values()
                            ):
                                if isinstance(capability, dict):
                                    capability.pop("listChanged", None)
                                    capability.pop("subscribe", None)
                    code = response.get("error", {}).get("code")
                    auth_status = response.get("error", {}).get("data", {})
                    auth_status = (
                        auth_status.get("http_status")
                        if isinstance(auth_status, dict)
                        else None
                    )
                    status = (
                        auth_status
                        if auth_status in {401, 403}
                        else 404
                        if code == -32601
                        else 401
                        if code == 1001
                        else 400
                        if code in {-32020, -32021, -32022, -32600, -32602}
                        else 200
                    )
                    self._write(status, response)
                except Exception:  # noqa: BLE001 - isolate failures at the HTTP boundary
                    _LOG.error("Streamable HTTP request dispatch failed")
                    self._write(500, _error(request, -32603, "Internal server error"))

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._http = _HTTPServer((self.host, self.port), Handler)
        self._http.daemon_threads = True
        try:
            self._http.serve_forever(poll_interval=0.05)
        finally:
            self._http.server_close()
            self._http = None

    def shutdown(self) -> None:
        features = self.server._modern_requests.features
        if features is not None:
            features.close()
        self._stopping.set()
        if self._http is not None:
            self._http.shutdown()


def run_mcp_streamable_http(
    server: MCPJsonRpcServer, host: str = "127.0.0.1", port: int = 8081
) -> None:
    MCPStreamableHTTPTransportServer(host, port, server).start()
