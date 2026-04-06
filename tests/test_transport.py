from __future__ import annotations

import io
import json
import pathlib
import socket
import sys
import threading
import time
import unittest
from urllib import request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.schema import MessageEnvelope
from mtp.transport.http import HTTPTransportServer
from mtp.transport.stdio import run_stdio_transport


class TransportTests(unittest.TestCase):
    def test_stdio_transport_roundtrip(self) -> None:
        old_stdin, old_stdout = sys.stdin, sys.stdout
        try:
            sys.stdin = io.StringIO('{"kind":"ping","payload":{"x":1},"metadata":{}}\n')
            out = io.StringIO()
            sys.stdout = out

            def handler(env: MessageEnvelope) -> MessageEnvelope:
                return MessageEnvelope.create(kind="pong", payload={"echo": env.payload})

            run_stdio_transport(handler)
            lines = [line for line in out.getvalue().splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            response = json.loads(lines[0])
            self.assertEqual(response["kind"], "pong")
            self.assertEqual(response["payload"]["echo"]["x"], 1)
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout

    def test_http_transport_roundtrip(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        _, port = sock.getsockname()
        sock.close()

        def handler(env: MessageEnvelope) -> MessageEnvelope:
            return MessageEnvelope.create(kind="ok", payload={"kind": env.kind})

        server = HTTPTransportServer("127.0.0.1", port, handler)
        thread = threading.Thread(target=server.start, daemon=True)
        thread.start()
        time.sleep(0.1)

        try:
            payload = MessageEnvelope.create(kind="hello", payload={"a": 1}).to_json().encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{port}",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8")
            data = json.loads(body)
            self.assertEqual(data["kind"], "ok")
            self.assertEqual(data["payload"]["kind"], "hello")
        finally:
            server.shutdown()
            thread.join(timeout=1)

    def test_stdio_transport_cancel_control(self) -> None:
        old_stdin, old_stdout = sys.stdin, sys.stdout
        try:
            sys.stdin = io.StringIO(
                '{"kind":"cancel","payload":{"request_id":"r1"},"metadata":{}}\n'
                '{"kind":"ping","payload":{},"metadata":{"request_id":"r1"}}\n'
            )
            out = io.StringIO()
            sys.stdout = out

            def handler(env: MessageEnvelope, cancel_checker=None) -> MessageEnvelope:
                if callable(cancel_checker) and cancel_checker():
                    return MessageEnvelope.create(kind="cancelled", payload={"request_id": "r1"})
                return MessageEnvelope.create(kind="pong", payload={"echo": env.payload})

            run_stdio_transport(handler)
            lines = [line for line in out.getvalue().splitlines() if line.strip()]
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            second = json.loads(lines[1])
            self.assertEqual(first["kind"], "cancel_ack")
            self.assertEqual(second["kind"], "cancelled")
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout

    def test_http_transport_cancel_control(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        _, port = sock.getsockname()
        sock.close()

        def handler(env: MessageEnvelope, cancel_checker=None) -> MessageEnvelope:
            if callable(cancel_checker) and cancel_checker():
                return MessageEnvelope.create(kind="cancelled", payload={"request_id": "h1"})
            return MessageEnvelope.create(kind="ok", payload={"kind": env.kind})

        server = HTTPTransportServer("127.0.0.1", port, handler)
        thread = threading.Thread(target=server.start, daemon=True)
        thread.start()
        time.sleep(0.1)

        try:
            cancel_payload = MessageEnvelope.create(
                kind="cancel",
                payload={"request_id": "h1"},
            ).to_json().encode("utf-8")
            cancel_req = request.Request(
                f"http://127.0.0.1:{port}",
                data=cancel_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(cancel_req, timeout=5) as resp:
                cancel_body = resp.read().decode("utf-8")
            cancel_data = json.loads(cancel_body)
            self.assertEqual(cancel_data["kind"], "cancel_ack")

            payload = MessageEnvelope.create(
                kind="work",
                payload={"x": 1},
                metadata={"request_id": "h1"},
            ).to_json().encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{port}",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8")
            data = json.loads(body)
            self.assertEqual(data["kind"], "cancelled")
        finally:
            server.shutdown()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
