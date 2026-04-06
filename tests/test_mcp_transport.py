from __future__ import annotations

import json
import pathlib
import socket
import sys
import threading
import time
import unittest
from urllib import parse, request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import MCPAuthDecision, MCPHTTPTransportServer, MCPJsonRpcServer, ToolRegistry, ToolSpec


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
        time.sleep(0.1)
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
        time.sleep(0.1)
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
        time.sleep(0.1)
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
        time.sleep(0.1)
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
        time.sleep(0.1)
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


if __name__ == "__main__":
    unittest.main()
