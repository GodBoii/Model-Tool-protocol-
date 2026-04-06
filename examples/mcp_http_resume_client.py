from __future__ import annotations

import json
from urllib import request


BASE = "http://127.0.0.1:8081"


def _post_json(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(f"{BASE}{path}", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(path: str, *, last_event_id: int | None = None) -> dict:
    req = request.Request(f"{BASE}{path}", method="GET")
    if last_event_id is not None:
        req.add_header("Last-Event-ID", str(last_event_id))
    with request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    _post_json("/rpc", {"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}})
    _post_json(
        "/rpc",
        {
            "jsonrpc": "2.0",
            "id": "call-1",
            "method": "tools/call",
            "params": {
                "name": "calc.add",
                "arguments": {"a": 2, "b": 3},
                "progressToken": "resume-demo",
            },
        },
    )

    first = _get_json("/events?limit=1&since_id=0")
    events = first.get("events", [])
    if not events:
        print("No events found.")
        return
    print("First page:", first)

    last_seen = int(events[-1]["event_id"])
    resumed = _get_json("/events?limit=20", last_event_id=last_seen)
    print("Resumed page:", resumed)


if __name__ == "__main__":
    main()
