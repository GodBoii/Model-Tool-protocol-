from __future__ import annotations

import asyncio
from collections import deque
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .mcp import MCPJsonRpcServer


class MCPHTTPTransportServer:
    """
    MCP-oriented HTTP transport with:
    - JSON-RPC POST endpoint (`/rpc`)
    - session header propagation (`MCP-Session-Id`)
    - bearer auth propagation into request params (`auth_token`)
    - event stream endpoint (`/events`) for progress notifications
    """

    def __init__(
        self,
        host: str,
        port: int,
        server: MCPJsonRpcServer,
        *,
        replay_window: int = 1000,
        sse_keepalive_seconds: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.server = server
        self._http: ThreadingHTTPServer | None = None
        self.replay_window = max(1, int(replay_window))
        self.sse_keepalive_seconds = max(1.0, float(sse_keepalive_seconds))
        self._events_cv = threading.Condition()
        self._events: "deque[dict[str, Any]]" = deque(maxlen=self.replay_window)
        self._next_event_id = 1
        self.server.add_progress_listener(self._on_progress)

    def _on_progress(self, event: dict[str, Any]) -> None:
        with self._events_cv:
            event_id = self._next_event_id
            self._next_event_id += 1
            payload = dict(event)
            payload["event_id"] = event_id
            payload["resume_token"] = str(event_id)
            payload.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            self._events.append(payload)
            self._events_cv.notify_all()

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

    def _latest_event_id(self) -> int:
        with self._events_cv:
            if not self._events:
                return 0
            return int(self._events[-1]["event_id"])

    def _events_since(self, since_id: int, *, limit: int) -> list[dict[str, Any]]:
        with self._events_cv:
            selected = [event for event in self._events if int(event.get("event_id", 0)) > since_id]
        return selected[:limit]

    def start(self) -> None:
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def _read_json_body(self) -> Any:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(content_length).decode("utf-8")
                return json.loads(raw)

            def _session_id(self) -> str | None:
                value = self.headers.get("MCP-Session-Id")
                if value and value.strip():
                    return value.strip()
                return None

            def _inject_auth_and_session(self, request_obj: dict[str, Any]) -> dict[str, Any]:
                params = request_obj.get("params")
                if not isinstance(params, dict):
                    params = {}
                params = dict(params)

                auth_header = self.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[len("Bearer ") :].strip()
                    if token:
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

            def _write_json(self, status: int, payload: Any) -> None:
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
                self.send_header("X-MCP-Resume-Token", str(outer._latest_event_id()))
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

            def _serve_sse(self, since_id: int) -> None:
                self.send_response(200)
                session_id = self._session_id()
                if session_id:
                    self.send_header("MCP-Session-Id", session_id)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-MCP-Resume-Token", str(outer._latest_event_id()))
                self.end_headers()

                last_sent = since_id
                try:
                    backlog = outer._events_since(last_sent, limit=outer.replay_window)
                    for event in backlog:
                        self._write_sse_event(event)
                        last_sent = max(last_sent, int(event.get("event_id", 0)))

                    while True:
                        with outer._events_cv:
                            wait_ok = outer._events_cv.wait(timeout=outer.sse_keepalive_seconds)
                            fresh = [
                                event for event in outer._events if int(event.get("event_id", 0)) > last_sent
                            ]
                        if fresh:
                            for event in fresh:
                                self._write_sse_event(event)
                                last_sent = max(last_sent, int(event.get("event_id", 0)))
                            continue
                        if not wait_ok:
                            self._write_sse_comment("keepalive")
                except (BrokenPipeError, ConnectionResetError):
                    return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path in {"/events/stream", "/events/sse"}:
                    qs = parse_qs(parsed.query)
                    since_id = outer._parse_resume_cursor(qs, self.headers)
                    self._serve_sse(since_id)
                    return
                if parsed.path != "/events":
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = parse_qs(parsed.query)
                limit_raw = qs.get("limit", ["20"])[0]
                try:
                    limit = max(1, min(200, int(limit_raw)))
                except Exception:
                    limit = 20
                since_id = outer._parse_resume_cursor(qs, self.headers)
                events = outer._events_since(since_id, limit=limit)
                latest_event_id = outer._latest_event_id()
                self._write_json(
                    200,
                    {
                        "events": events,
                        "next_resume_token": str(latest_event_id),
                        "latest_event_id": latest_event_id,
                    },
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
    """

    def __init__(self, host: str, port: int, server: MCPJsonRpcServer) -> None:
        self.host = host
        self.port = port
        self.server = server
        self._server: Any = None
        self._clients: set[Any] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.server.add_progress_listener(self._on_progress)

    def _on_progress(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast_progress(event), loop)

    async def _broadcast_progress(self, event: dict[str, Any]) -> None:
        if not self._clients:
            return
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": event,
        }
        raw = json.dumps(payload, default=str)
        dead: list[Any] = []
        for ws in self._clients:
            try:
                await ws.send(raw)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def start(self) -> None:
        try:
            import websockets
        except Exception as exc:  # noqa: BLE001
            raise ImportError("websockets package is required for MCPWebSocketTransportServer.") from exc

        async def _handle(websocket: Any) -> None:
            self._clients.add(websocket)
            try:
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
                    response = await self.server.ahandle_request(data)
                    if response is not None:
                        await websocket.send(json.dumps(response, default=str))
            finally:
                self._clients.discard(websocket)

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
