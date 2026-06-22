from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .mcp import MCPJsonRpcServer


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _token_fingerprint(token: Any) -> str | None:
    normalized = _normalize_optional_string(token)
    if normalized is None:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _parse_bearer_token(auth_header: Any) -> str | None:
    if not isinstance(auth_header, str):
        return None
    if not auth_header.startswith("Bearer "):
        return None
    return _normalize_optional_string(auth_header[len("Bearer ") :])


class _ProgressReplayStore:
    def __init__(
        self,
        *,
        replay_window: int = 1000,
        replay_ttl_seconds: float | None = 3600.0,
        persist_path: str | Path | None = None,
    ) -> None:
        self.replay_window = max(1, int(replay_window))
        self.replay_ttl_seconds = None if replay_ttl_seconds is None else max(1.0, float(replay_ttl_seconds))
        self.persist_path = Path(persist_path).resolve() if persist_path is not None else None
        self._condition = threading.Condition()
        self._events: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._load_persisted_locked()

    def _load_persisted_locked(self) -> None:
        if self.persist_path is None or not self.persist_path.exists():
            return
        try:
            payload = json.loads(self.persist_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        events = payload.get("events")
        if isinstance(events, list):
            self._events = [dict(item) for item in events if isinstance(item, dict)]
        next_event_id = payload.get("next_event_id")
        if isinstance(next_event_id, int) and next_event_id > 0:
            self._next_event_id = next_event_id
        else:
            max_seen = 0
            for event in self._events:
                try:
                    max_seen = max(max_seen, int(event.get("event_id", 0)))
                except Exception:
                    continue
            self._next_event_id = max_seen + 1
        self._prune_locked()

    def _persist_locked(self) -> None:
        if self.persist_path is None:
            return
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"next_event_id": self._next_event_id, "events": self._events}
        tmp_path = self.persist_path.with_suffix(self.persist_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        tmp_path.replace(self.persist_path)

    def _prune_locked(self) -> None:
        now = time.time()
        if self.replay_ttl_seconds is not None:
            threshold = now - self.replay_ttl_seconds
            self._events = [event for event in self._events if float(event.get("created_at_epoch", 0.0)) >= threshold]
        if len(self._events) > self.replay_window:
            self._events = self._events[-self.replay_window :]

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._condition:
            payload = dict(event)
            event_id = self._next_event_id
            self._next_event_id += 1
            payload["event_id"] = event_id
            payload["resume_token"] = str(event_id)
            payload.setdefault("timestamp", _now_iso())
            payload["created_at_epoch"] = float(payload.get("created_at_epoch") or time.time())
            self._events.append(payload)
            self._prune_locked()
            self._persist_locked()
            self._condition.notify_all()
            return dict(payload)

    def _scope_matches(
        self,
        event: dict[str, Any],
        *,
        session_id: str | None,
        auth_fingerprint: str | None,
    ) -> bool:
        event_session = _normalize_optional_string(event.get("sessionId"))
        event_auth = _normalize_optional_string(event.get("authFingerprint"))

        req_session = _normalize_optional_string(session_id)
        req_auth = _normalize_optional_string(auth_fingerprint)

        if req_session is None:
            if event_session is not None:
                return False
        elif event_session != req_session:
            return False

        if req_auth is None:
            return event_auth is None
        if event_auth is None:
            return True
        return event_auth == req_auth

    def events_since(
        self,
        since_id: int,
        *,
        limit: int,
        session_id: str | None = None,
        auth_fingerprint: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._condition:
            self._prune_locked()
            selected = [
                dict(event)
                for event in self._events
                if int(event.get("event_id", 0)) > since_id
                and self._scope_matches(event, session_id=session_id, auth_fingerprint=auth_fingerprint)
            ]
        return selected[: max(1, int(limit))]

    def latest_event_id(
        self,
        *,
        session_id: str | None = None,
        auth_fingerprint: str | None = None,
    ) -> int:
        with self._condition:
            self._prune_locked()
            ids = [
                int(event.get("event_id", 0))
                for event in self._events
                if self._scope_matches(event, session_id=session_id, auth_fingerprint=auth_fingerprint)
            ]
        return max(ids) if ids else 0

    def wait_for_new_events(
        self,
        *,
        after_id: int,
        timeout_seconds: float,
        session_id: str | None = None,
        auth_fingerprint: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._condition:
            self._condition.wait(timeout=max(0.05, timeout_seconds))
            self._prune_locked()
            return [
                dict(event)
                for event in self._events
                if int(event.get("event_id", 0)) > after_id
                and self._scope_matches(event, session_id=session_id, auth_fingerprint=auth_fingerprint)
            ]

    def scope_matches_event(
        self,
        event: dict[str, Any],
        *,
        session_id: str | None,
        auth_fingerprint: str | None,
    ) -> bool:
        with self._condition:
            return self._scope_matches(event, session_id=session_id, auth_fingerprint=auth_fingerprint)


class MCPHTTPTransportServer:
    """
    MCP-oriented HTTP transport with:
    - JSON-RPC POST endpoint (`/rpc`)
    - session header propagation (`MCP-Session-Id`)
    - bearer auth propagation into request params (`auth_token`)
    - event polling endpoint (`/events`) with resume cursors
    - SSE endpoint (`/events/stream` and `/events/sse`) with replay and keepalive
    """

    def __init__(
        self,
        host: str,
        port: int,
        server: MCPJsonRpcServer,
        *,
        replay_window: int = 1000,
        replay_ttl_seconds: float | None = 3600.0,
        replay_store_path: str | Path | None = None,
        replay_store: _ProgressReplayStore | None = None,
        sse_keepalive_seconds: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.server = server
        self._http: ThreadingHTTPServer | None = None
        self.sse_keepalive_seconds = max(1.0, float(sse_keepalive_seconds))
        self._replay = replay_store or _ProgressReplayStore(
            replay_window=replay_window,
            replay_ttl_seconds=replay_ttl_seconds,
            persist_path=replay_store_path,
        )
        self.server.add_progress_listener(self._on_progress)

    def _on_progress(self, event: dict[str, Any]) -> None:
        self._replay.append(event)

    def _parse_resume_cursor(self, qs: dict[str, list[str]], headers: Any) -> int:
        raw_cursor = None
        for key in ("since_id", "last_event_id", "resume_token", "since"):
            values = qs.get(key)
            if values and values[0]:
                raw_cursor = values[0]
                break
        if raw_cursor is None:
            header_cursor = headers.get("Last-Event-ID")
            if header_cursor:
                raw_cursor = header_cursor
        if raw_cursor is None:
            return 0
        try:
            return max(0, int(str(raw_cursor)))
        except Exception:
            return 0

    def _scope_from_http_request(self, *, headers: Any, qs: dict[str, list[str]]) -> tuple[str | None, str | None]:
        session_id = _normalize_optional_string(headers.get("MCP-Session-Id"))
        if session_id is None:
            for key in ("session_id", "sessionId"):
                values = qs.get(key)
                if values and values[0]:
                    session_id = _normalize_optional_string(values[0])
                    break

        token = _parse_bearer_token(headers.get("Authorization", ""))
        return session_id, _token_fingerprint(token)

    def start(self) -> None:
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def _read_json_body(self) -> Any:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(content_length).decode("utf-8")
                return json.loads(raw)

            def _session_id(self) -> str | None:
                return _normalize_optional_string(self.headers.get("MCP-Session-Id"))

            def _inject_auth_and_session(self, request_obj: dict[str, Any]) -> dict[str, Any]:
                params = request_obj.get("params")
                if not isinstance(params, dict):
                    params = {}
                params = dict(params)

                token = _parse_bearer_token(self.headers.get("Authorization", ""))
                if token is not None:
                    params.setdefault("auth_token", token)

                session_id = self._session_id()
                if session_id:
                    meta = request_obj.get("meta")
                    if not isinstance(meta, dict):
                        meta = {}
                    meta = dict(meta)
                    meta["sessionId"] = session_id
                    request_obj["meta"] = meta
                    params.setdefault("sessionId", session_id)

                request_obj["params"] = params
                return request_obj

            def _write_json(self, status: int, payload: Any, *, resume_token: str | None = None) -> None:
                body = json.dumps(payload, default=str).encode("utf-8")
                self.send_response(status)
                session_id = self._session_id()
                if session_id:
                    self.send_header("MCP-Session-Id", session_id)
                if (
                    isinstance(payload, dict)
                    and isinstance(payload.get("error"), dict)
                    and isinstance(payload["error"].get("data"), dict)
                ):
                    challenge = payload["error"]["data"].get("www_authenticate")
                    if isinstance(challenge, str) and challenge:
                        self.send_header("WWW-Authenticate", challenge)
                self.send_header("Content-Type", "application/json")
                if resume_token is not None:
                    self.send_header("X-MCP-Resume-Token", resume_token)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _write_sse_event(self, event: dict[str, Any]) -> None:
                event_id = int(event.get("event_id", 0))
                body = (
                    f"id: {event_id}\n"
                    "event: progress\n"
                    f"data: {json.dumps(event, default=str)}\n\n"
                ).encode("utf-8")
                self.wfile.write(body)
                self.wfile.flush()

            def _write_sse_comment(self, text: str) -> None:
                body = f": {text}\n\n".encode("utf-8")
                self.wfile.write(body)
                self.wfile.flush()

            def _serve_sse(
                self,
                *,
                since_id: int,
                session_id: str | None,
                auth_fingerprint: str | None,
            ) -> None:
                self.send_response(200)
                if session_id:
                    self.send_header("MCP-Session-Id", session_id)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                scoped_latest = outer._replay.latest_event_id(
                    session_id=session_id,
                    auth_fingerprint=auth_fingerprint,
                )
                self.send_header("X-MCP-Resume-Token", str(scoped_latest))
                self.end_headers()

                last_sent = since_id
                try:
                    backlog = outer._replay.events_since(
                        last_sent,
                        limit=outer._replay.replay_window,
                        session_id=session_id,
                        auth_fingerprint=auth_fingerprint,
                    )
                    for event in backlog:
                        self._write_sse_event(event)
                        last_sent = max(last_sent, int(event.get("event_id", 0)))

                    while True:
                        fresh = outer._replay.wait_for_new_events(
                            after_id=last_sent,
                            timeout_seconds=outer.sse_keepalive_seconds,
                            session_id=session_id,
                            auth_fingerprint=auth_fingerprint,
                        )
                        if fresh:
                            for event in fresh:
                                self._write_sse_event(event)
                                last_sent = max(last_sent, int(event.get("event_id", 0)))
                            continue
                        self._write_sse_comment("keepalive")
                except (BrokenPipeError, ConnectionResetError):
                    return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                session_id, auth_fingerprint = outer._scope_from_http_request(headers=self.headers, qs=qs)
                since_id = outer._parse_resume_cursor(qs, self.headers)

                if parsed.path in {"/events/stream", "/events/sse"}:
                    self._serve_sse(
                        since_id=since_id,
                        session_id=session_id,
                        auth_fingerprint=auth_fingerprint,
                    )
                    return
                if parsed.path != "/events":
                    self.send_response(404)
                    self.end_headers()
                    return
                limit_raw = qs.get("limit", ["20"])[0]
                try:
                    limit = max(1, min(200, int(limit_raw)))
                except Exception:
                    limit = 20

                events = outer._replay.events_since(
                    since_id,
                    limit=limit,
                    session_id=session_id,
                    auth_fingerprint=auth_fingerprint,
                )
                scoped_latest = outer._replay.latest_event_id(
                    session_id=session_id,
                    auth_fingerprint=auth_fingerprint,
                )
                self._write_json(
                    200,
                    {
                        "events": events,
                        "next_resume_token": str(scoped_latest),
                        "latest_event_id": scoped_latest,
                    },
                    resume_token=str(scoped_latest),
                )

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path not in {"/", "/rpc"}:
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    payload = self._read_json_body()
                    if isinstance(payload, list):
                        responses: list[dict[str, Any]] = []
                        for item in payload:
                            if not isinstance(item, dict):
                                continue
                            req = self._inject_auth_and_session(dict(item))
                            result = outer.server.handle_request(req)
                            if result is not None:
                                responses.append(result)
                        self._write_json(200, responses)
                        return

                    if not isinstance(payload, dict):
                        self._write_json(400, {"error": "Invalid JSON-RPC payload"})
                        return

                    request_obj = self._inject_auth_and_session(dict(payload))
                    result = outer.server.handle_request(request_obj)
                    if result is None:
                        self.send_response(204)
                        self.end_headers()
                        return
                    self._write_json(200, result)
                except Exception as exc:  # noqa: BLE001
                    self._write_json(
                        400,
                        {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}},
                    )

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                return

        self._http = ThreadingHTTPServer((self.host, self.port), _Handler)
        self._http.serve_forever()

    def shutdown(self) -> None:
        if self._http is not None:
            self._http.shutdown()


class MCPWebSocketTransportServer:
    """
    MCP-oriented websocket transport with:
    - JSON-RPC request/response messages
    - async handling through `MCPJsonRpcServer.ahandle_request`
    - progress notifications broadcast to connected clients
    - replay via `events/replay` method and connection-query resume cursors
    """

    def __init__(
        self,
        host: str,
        port: int,
        server: MCPJsonRpcServer,
        *,
        replay_window: int = 1000,
        replay_ttl_seconds: float | None = 3600.0,
        replay_store_path: str | Path | None = None,
        replay_store: _ProgressReplayStore | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.server = server
        self._server: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: dict[Any, tuple[str | None, str | None]] = {}
        self._replay = replay_store or _ProgressReplayStore(
            replay_window=replay_window,
            replay_ttl_seconds=replay_ttl_seconds,
            persist_path=replay_store_path,
        )
        self.server.add_progress_listener(self._on_progress)

    def _on_progress(self, event: dict[str, Any]) -> None:
        persisted = self._replay.append(event)
        loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast_progress(persisted), loop)

    def _parse_resume_cursor(self, qs: dict[str, list[str]], headers: Any) -> int:
        raw_cursor = None
        for key in ("since_id", "last_event_id", "resume_token", "since"):
            values = qs.get(key)
            if values and values[0]:
                raw_cursor = values[0]
                break
        if raw_cursor is None:
            header_cursor = headers.get("Last-Event-ID")
            if header_cursor:
                raw_cursor = header_cursor
        if raw_cursor is None:
            return 0
        try:
            return max(0, int(str(raw_cursor)))
        except Exception:
            return 0

    def _scope_from_ws(self, websocket: Any) -> tuple[str | None, str | None, int]:
        path = getattr(websocket, "path", "") or ""
        parsed = urlparse(path)
        qs = parse_qs(parsed.query)
        headers = getattr(websocket, "request_headers", {})

        session_id = _normalize_optional_string(headers.get("MCP-Session-Id"))
        if session_id is None:
            for key in ("session_id", "sessionId"):
                values = qs.get(key)
                if values and values[0]:
                    session_id = _normalize_optional_string(values[0])
                    break

        token = _parse_bearer_token(headers.get("Authorization", ""))
        auth_fingerprint = _token_fingerprint(token)
        since_id = self._parse_resume_cursor(qs, headers)
        return session_id, auth_fingerprint, since_id

    def _request_cursor_from_params(self, params: dict[str, Any]) -> int:
        for key in ("since_id", "last_event_id", "resume_token", "since"):
            value = params.get(key)
            if value is None:
                continue
            try:
                return max(0, int(str(value)))
            except Exception:
                continue
        return 0

    def _inject_scope_into_request(
        self,
        request_obj: dict[str, Any],
        *,
        session_id: str | None,
        auth_fingerprint: str | None,
    ) -> dict[str, Any]:
        params = request_obj.get("params")
        if not isinstance(params, dict):
            params = {}
        params = dict(params)
        if session_id:
            params.setdefault("sessionId", session_id)
            meta = request_obj.get("meta")
            if not isinstance(meta, dict):
                meta = {}
            meta = dict(meta)
            meta.setdefault("sessionId", session_id)
            request_obj["meta"] = meta
        request_obj["params"] = params
        # `auth_fingerprint` is not injected back into params; auth tokens should
        # come from client-provided values.
        return request_obj

    def _scope_from_request_payload(
        self,
        request_obj: dict[str, Any],
        fallback_scope: tuple[str | None, str | None],
    ) -> tuple[str | None, str | None]:
        current_session, current_auth = fallback_scope
        params = request_obj.get("params")
        if not isinstance(params, dict):
            return fallback_scope
        req_session = _normalize_optional_string(params.get("sessionId")) or _normalize_optional_string(
            params.get("session_id")
        )
        req_auth = _token_fingerprint(params.get("auth_token"))
        return req_session or current_session, req_auth or current_auth

    async def _send_progress_notification(self, websocket: Any, event: dict[str, Any]) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": event,
        }
        await websocket.send(json.dumps(payload, default=str))

    async def _broadcast_progress(self, event: dict[str, Any]) -> None:
        if not self._clients:
            return
        dead: list[Any] = []
        for ws, scope in self._clients.items():
            session_id, auth_fingerprint = scope
            if not self._replay.scope_matches_event(
                event,
                session_id=session_id,
                auth_fingerprint=auth_fingerprint,
            ):
                continue
            try:
                await self._send_progress_notification(ws, event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.pop(ws, None)

    async def _send_replay(
        self,
        websocket: Any,
        *,
        since_id: int,
        scope: tuple[str | None, str | None],
        limit: int = 200,
    ) -> None:
        session_id, auth_fingerprint = scope
        replay = self._replay.events_since(
            since_id,
            limit=max(1, limit),
            session_id=session_id,
            auth_fingerprint=auth_fingerprint,
        )
        for event in replay:
            await self._send_progress_notification(websocket, event)

    async def start(self) -> None:
        try:
            import websockets
        except Exception as exc:  # noqa: BLE001
            raise ImportError("websockets package is required for MCPWebSocketTransportServer.") from exc

        async def _handle(websocket: Any) -> None:
            session_id, auth_fingerprint, connect_since_id = self._scope_from_ws(websocket)
            scope = (session_id, auth_fingerprint)
            self._clients[websocket] = scope
            try:
                if connect_since_id >= 0:
                    await self._send_replay(websocket, since_id=connect_since_id, scope=scope)
                async for raw in websocket:
                    try:
                        data = json.loads(raw)
                    except Exception as exc:  # noqa: BLE001
                        await websocket.send(
                            json.dumps(
                                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}},
                                default=str,
                            )
                        )
                        continue
                    if not isinstance(data, dict):
                        await websocket.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": None,
                                    "error": {"code": -32600, "message": "Invalid Request: expected object"},
                                },
                                default=str,
                            )
                        )
                        continue

                    scope = self._scope_from_request_payload(data, self._clients.get(websocket, (None, None)))
                    self._clients[websocket] = scope
                    method = data.get("method")
                    params = data.get("params") if isinstance(data.get("params"), dict) else {}
                    if method == "events/replay":
                        request_id = data.get("id")
                        since_id = self._request_cursor_from_params(params)
                        limit = params.get("limit", 200)
                        try:
                            safe_limit = max(1, min(1000, int(limit)))
                        except Exception:
                            safe_limit = 200
                        session_id_scope, auth_scope = scope
                        events = self._replay.events_since(
                            since_id,
                            limit=safe_limit,
                            session_id=session_id_scope,
                            auth_fingerprint=auth_scope,
                        )
                        latest_event_id = self._replay.latest_event_id(
                            session_id=session_id_scope,
                            auth_fingerprint=auth_scope,
                        )
                        if request_id is not None:
                            await websocket.send(
                                json.dumps(
                                    {
                                        "jsonrpc": "2.0",
                                        "id": request_id,
                                        "result": {
                                            "events": events,
                                            "next_resume_token": str(latest_event_id),
                                            "latest_event_id": latest_event_id,
                                        },
                                    },
                                    default=str,
                                )
                            )
                        continue

                    request_obj = self._inject_scope_into_request(
                        dict(data),
                        session_id=scope[0],
                        auth_fingerprint=scope[1],
                    )
                    response = await self.server.ahandle_request(request_obj)
                    if response is not None:
                        await websocket.send(json.dumps(response, default=str))
            finally:
                self._clients.pop(websocket, None)

        self._loop = asyncio.get_running_loop()
        self._server = await websockets.serve(_handle, self.host, self.port)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        await self._server.wait_closed()

    async def shutdown(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None


def run_mcp_http(server: MCPJsonRpcServer, host: str = "127.0.0.1", port: int = 8081) -> None:
    MCPHTTPTransportServer(host=host, port=port, server=server).start()


def run_mcp_ws(server: MCPJsonRpcServer, host: str = "127.0.0.1", port: int = 8766) -> None:
    transport = MCPWebSocketTransportServer(host=host, port=port, server=server)

    async def _runner() -> None:
        await transport.start()
        await transport.serve_forever()

    asyncio.run(_runner())
