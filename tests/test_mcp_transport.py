from __future__ import annotations

import asyncio
import json
import pathlib
import socket
import sys
import threading
import uuid
import unittest
from urllib import parse, request

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import (
    MCPAuthDecision,
    MCPHTTPTransportServer,
    MCPJsonRpcServer,
    MCPWebSocketTransportServer,
    ToolRegistry,
    ToolSpec,
)
from tests.harness_utils import wait_for_tcp_listener

pytestmark = pytest.mark.integration


class MCPHTTPTransportTests(unittest.TestCase):
    def _free_port(self) -> int:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        _, port = sock.getsockname()
        sock.close()
        return int(port)

    def _post_json(self, url: str, payload: object, headers: dict[str, str] | None = None) -> tuple[int, dict, dict]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        with request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8")) if resp.status != 204 else {}
            return resp.status, data, dict(resp.headers)

    def _get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
        req = request.Request(url, method="GET")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        with request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data

    def test_http_transport_auth_and_session_headers(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(ToolSpec(name="calc.add", description="add"), lambda a, b: a + b)
        server = MCPJsonRpcServer(tools=registry, require_auth=True, auth_token="secret")

        port = self._free_port()
        transport = MCPHTTPTransportServer("127.0.0.1", port, server)
        thread = threading.Thread(target=transport.start, daemon=True)
        thread.start()
        wait_for_tcp_listener("127.0.0.1", int(port), timeout_seconds=5)
        try:
            status, payload, headers = self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}},
                headers={
                    "Authorization": "Bearer secret",
                    "MCP-Session-Id": "sess-1",
                },
            )
            self.assertEqual(status, 200)
            self.assertIn("result", payload)
            self.assertEqual(headers.get("MCP-Session-Id"), "sess-1")
        finally:
            transport.shutdown()
            thread.join(timeout=1)

    def test_http_transport_sets_www_authenticate_header(self) -> None:
        class _OAuthProvider:
            def authorize(self, token, request, context):
                return MCPAuthDecision(
                    allowed=False,
                    message="Unauthorized",
                    www_authenticate='Bearer realm="mtp", error="invalid_token"',
                )

        registry = ToolRegistry()
        registry.register_tool(ToolSpec(name="calc.add", description="add"), lambda a, b: a + b)
        server = MCPJsonRpcServer(tools=registry, auth_provider=_OAuthProvider())

        port = self._free_port()
        transport = MCPHTTPTransportServer("127.0.0.1", port, server)
        thread = threading.Thread(target=transport.start, daemon=True)
        thread.start()
        wait_for_tcp_listener("127.0.0.1", int(port), timeout_seconds=5)
        try:
            status, payload, headers = self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}},
            )
            self.assertEqual(status, 200)
            self.assertIn("error", payload)
            self.assertEqual(headers.get("WWW-Authenticate"), 'Bearer realm="mtp", error="invalid_token"')
        finally:
            transport.shutdown()
            thread.join(timeout=1)

    def test_http_transport_events_and_batch(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(ToolSpec(name="calc.add", description="add"), lambda a, b: a + b)
        server = MCPJsonRpcServer(tools=registry)

        port = self._free_port()
        transport = MCPHTTPTransportServer("127.0.0.1", port, server)
        thread = threading.Thread(target=transport.start, daemon=True)
        thread.start()
        wait_for_tcp_listener("127.0.0.1", int(port), timeout_seconds=5)
        try:
            status, batch_payload, _headers = self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                [
                    {"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": "tools-1", "method": "tools/list", "params": {}},
                ],
            )
            self.assertEqual(status, 200)
            self.assertIsInstance(batch_payload, list)
            self.assertEqual(len(batch_payload), 2)

            self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {
                    "jsonrpc": "2.0",
                    "id": "call-1",
                    "method": "tools/call",
                    "params": {
                        "name": "calc.add",
                        "arguments": {"a": 1, "b": 2},
                        "progressToken": "p-1",
                    },
                },
            )

            status_events, events_payload = self._get_json(
                f"http://127.0.0.1:{port}/events?{parse.urlencode({'limit': 20})}"
            )
            self.assertEqual(status_events, 200)
            events = events_payload.get("events", [])
            self.assertTrue(any(e.get("progressToken") == "p-1" for e in events))
        finally:
            transport.shutdown()
            thread.join(timeout=1)

    def test_http_transport_events_support_resume_cursor(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(ToolSpec(name="calc.add", description="add"), lambda a, b: a + b)
        server = MCPJsonRpcServer(tools=registry)

        port = self._free_port()
        transport = MCPHTTPTransportServer("127.0.0.1", port, server)
        thread = threading.Thread(target=transport.start, daemon=True)
        thread.start()
        wait_for_tcp_listener("127.0.0.1", int(port), timeout_seconds=5)
        try:
            self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}},
            )
            self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {
                    "jsonrpc": "2.0",
                    "id": "call-1",
                    "method": "tools/call",
                    "params": {"name": "calc.add", "arguments": {"a": 1, "b": 2}, "progressToken": "p-1"},
                },
            )
            self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {
                    "jsonrpc": "2.0",
                    "id": "call-2",
                    "method": "tools/call",
                    "params": {"name": "calc.add", "arguments": {"a": 3, "b": 4}, "progressToken": "p-2"},
                },
            )

            status_1, payload_1 = self._get_json(f"http://127.0.0.1:{port}/events?limit=1")
            self.assertEqual(status_1, 200)
            first_batch = payload_1.get("events", [])
            self.assertEqual(len(first_batch), 1)
            first_event_id = int(first_batch[0]["event_id"])

            status_2, payload_2 = self._get_json(
                f"http://127.0.0.1:{port}/events?{parse.urlencode({'since_id': first_event_id, 'limit': 20})}"
            )
            self.assertEqual(status_2, 200)
            remaining = payload_2.get("events", [])
            self.assertTrue(all(int(evt["event_id"]) > first_event_id for evt in remaining))
            self.assertIn("next_resume_token", payload_2)
            self.assertIn("latest_event_id", payload_2)

            # Header-based cursor should behave the same way.
            status_3, payload_3 = self._get_json(
                f"http://127.0.0.1:{port}/events?limit=20",
                headers={"Last-Event-ID": str(first_event_id)},
            )
            self.assertEqual(status_3, 200)
            header_remaining = payload_3.get("events", [])
            self.assertTrue(all(int(evt["event_id"]) > first_event_id for evt in header_remaining))
        finally:
            transport.shutdown()
            thread.join(timeout=1)

    def test_http_transport_sse_stream_replays_from_cursor(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(ToolSpec(name="calc.add", description="add"), lambda a, b: a + b)
        server = MCPJsonRpcServer(tools=registry)

        port = self._free_port()
        transport = MCPHTTPTransportServer("127.0.0.1", port, server)
        thread = threading.Thread(target=transport.start, daemon=True)
        thread.start()
        wait_for_tcp_listener("127.0.0.1", int(port), timeout_seconds=5)
        try:
            self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}},
            )
            self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {
                    "jsonrpc": "2.0",
                    "id": "call-1",
                    "method": "tools/call",
                    "params": {"name": "calc.add", "arguments": {"a": 2, "b": 3}, "progressToken": "p-sse"},
                },
            )

            req = request.Request(
                f"http://127.0.0.1:{port}/events/stream?since_id=0",
                method="GET",
                headers={"Accept": "text/event-stream"},
            )
            with request.urlopen(req, timeout=5) as resp:
                content_type = resp.headers.get("Content-Type", "")
                self.assertTrue(content_type.startswith("text/event-stream"))
                lines: list[str] = []
                for _ in range(12):
                    raw = resp.readline().decode("utf-8", errors="replace")
                    if not raw:
                        break
                    line = raw.strip()
                    lines.append(line)
                    if line.startswith("data: "):
                        break

                data_lines = [line for line in lines if line.startswith("data: ")]
                self.assertTrue(data_lines)
                event_payload = json.loads(data_lines[0][len("data: ") :])
                self.assertEqual(event_payload.get("progressToken"), "p-sse")
                self.assertIn("event_id", event_payload)
                self.assertIn("resume_token", event_payload)
        finally:
            transport.shutdown()
            thread.join(timeout=1)

    def test_http_transport_replay_persists_across_restart(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(ToolSpec(name="calc.add", description="add"), lambda a, b: a + b)
        server = MCPJsonRpcServer(tools=registry)

        tmp_dir = pathlib.Path("tmp")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        replay_path = tmp_dir / f"mcp_replay_{uuid.uuid4().hex}.json"

        port_1 = self._free_port()
        transport_1 = MCPHTTPTransportServer("127.0.0.1", port_1, server, replay_store_path=replay_path)
        thread_1 = threading.Thread(target=transport_1.start, daemon=True)
        thread_1.start()
        wait_for_tcp_listener("127.0.0.1", int(port_1), timeout_seconds=5)
        try:
            self._post_json(
                f"http://127.0.0.1:{port_1}/rpc",
                {"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}},
            )
            self._post_json(
                f"http://127.0.0.1:{port_1}/rpc",
                {
                    "jsonrpc": "2.0",
                    "id": "call-1",
                    "method": "tools/call",
                    "params": {"name": "calc.add", "arguments": {"a": 5, "b": 6}, "progressToken": "persist-1"},
                },
            )
        finally:
            transport_1.shutdown()
            thread_1.join(timeout=1)

        port_2 = self._free_port()
        transport_2 = MCPHTTPTransportServer("127.0.0.1", port_2, server, replay_store_path=replay_path)
        thread_2 = threading.Thread(target=transport_2.start, daemon=True)
        thread_2.start()
        wait_for_tcp_listener("127.0.0.1", int(port_2), timeout_seconds=5)
        try:
            status, payload = self._get_json(f"http://127.0.0.1:{port_2}/events?limit=20&since_id=0")
            self.assertEqual(status, 200)
            events = payload.get("events", [])
            self.assertTrue(any(evt.get("progressToken") == "persist-1" for evt in events))
        finally:
            transport_2.shutdown()
            thread_2.join(timeout=1)
            try:
                replay_path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_http_transport_scopes_events_by_session_and_auth(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(ToolSpec(name="calc.add", description="add"), lambda a, b: a + b)
        server = MCPJsonRpcServer(tools=registry)

        port = self._free_port()
        transport = MCPHTTPTransportServer("127.0.0.1", port, server)
        thread = threading.Thread(target=transport.start, daemon=True)
        thread.start()
        wait_for_tcp_listener("127.0.0.1", int(port), timeout_seconds=5)
        try:
            self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {"jsonrpc": "2.0", "id": "init-a", "method": "initialize", "params": {}},
                headers={"Authorization": "Bearer token-a", "MCP-Session-Id": "sess-a"},
            )
            self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {
                    "jsonrpc": "2.0",
                    "id": "call-a",
                    "method": "tools/call",
                    "params": {"name": "calc.add", "arguments": {"a": 1, "b": 1}, "progressToken": "scope-a"},
                },
                headers={"Authorization": "Bearer token-a", "MCP-Session-Id": "sess-a"},
            )

            self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {"jsonrpc": "2.0", "id": "init-b", "method": "initialize", "params": {}},
                headers={"Authorization": "Bearer token-b", "MCP-Session-Id": "sess-b"},
            )
            self._post_json(
                f"http://127.0.0.1:{port}/rpc",
                {
                    "jsonrpc": "2.0",
                    "id": "call-b",
                    "method": "tools/call",
                    "params": {"name": "calc.add", "arguments": {"a": 2, "b": 2}, "progressToken": "scope-b"},
                },
                headers={"Authorization": "Bearer token-b", "MCP-Session-Id": "sess-b"},
            )

            status_a, payload_a = self._get_json(
                f"http://127.0.0.1:{port}/events?limit=20",
                headers={"Authorization": "Bearer token-a", "MCP-Session-Id": "sess-a"},
            )
            self.assertEqual(status_a, 200)
            events_a = payload_a.get("events", [])
            self.assertTrue(any(evt.get("progressToken") == "scope-a" for evt in events_a))
            self.assertFalse(any(evt.get("progressToken") == "scope-b" for evt in events_a))

            status_b, payload_b = self._get_json(
                f"http://127.0.0.1:{port}/events?limit=20",
                headers={"Authorization": "Bearer token-b", "MCP-Session-Id": "sess-b"},
            )
            self.assertEqual(status_b, 200)
            events_b = payload_b.get("events", [])
            self.assertTrue(any(evt.get("progressToken") == "scope-b" for evt in events_b))
            self.assertFalse(any(evt.get("progressToken") == "scope-a" for evt in events_b))
        finally:
            transport.shutdown()
            thread.join(timeout=1)


class MCPWebSocketTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_replay_method_returns_progress_backlog(self) -> None:
        try:
            import websockets
        except Exception:
            self.skipTest("websockets package is not installed")
            return

        registry = ToolRegistry()
        registry.register_tool(ToolSpec(name="calc.add", description="add"), lambda a, b: a + b)
        server = MCPJsonRpcServer(tools=registry)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        _, port = sock.getsockname()
        sock.close()

        transport = MCPWebSocketTransportServer("127.0.0.1", int(port), server)
        await transport.start()
        try:
            uri = f"ws://127.0.0.1:{int(port)}?since_id=0&session_id=ws-sess"
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}}))
                _ = await ws.recv()

                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "call-1",
                            "method": "tools/call",
                            "params": {
                                "name": "calc.add",
                                "arguments": {"a": 10, "b": 20},
                                "progressToken": "ws-progress",
                                "sessionId": "ws-sess",
                            },
                        }
                    )
                )

                # Consume a few frames to allow progress + result messages to flow.
                received: list[dict] = []
                for _ in range(6):
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                    payload = json.loads(raw)
                    received.append(payload)
                    if payload.get("id") == "call-1":
                        break

                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "replay-1",
                            "method": "events/replay",
                            "params": {"since_id": 0, "limit": 20},
                        }
                    )
                )
                replay_payload = None
                for _ in range(8):
                    replay_raw = await asyncio.wait_for(ws.recv(), timeout=3)
                    candidate = json.loads(replay_raw)
                    if candidate.get("id") == "replay-1":
                        replay_payload = candidate
                        break
                self.assertIsNotNone(replay_payload)
                assert replay_payload is not None
                self.assertEqual(replay_payload.get("id"), "replay-1")
                events = replay_payload.get("result", {}).get("events", [])
                self.assertTrue(any(evt.get("progressToken") == "ws-progress" for evt in events))
                self.assertIn("latest_event_id", replay_payload.get("result", {}))
                self.assertIn("next_resume_token", replay_payload.get("result", {}))
        finally:
            await transport.shutdown()


if __name__ == "__main__":
    unittest.main()
