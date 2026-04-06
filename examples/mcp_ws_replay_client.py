from __future__ import annotations

import asyncio
import json


URI = "ws://127.0.0.1:8766?since_id=0&session_id=demo-session"


async def main() -> None:
    try:
        import websockets
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Install websockets: pip install websockets") from exc

    async with websockets.connect(URI) as ws:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}}))
        print("initialize:", await ws.recv())

        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "call-1",
                    "method": "tools/call",
                    "params": {
                        "name": "calc.add",
                        "arguments": {"a": 7, "b": 8},
                        "progressToken": "ws-demo",
                        "sessionId": "demo-session",
                    },
                }
            )
        )

        for _ in range(4):
            print("frame:", await ws.recv())

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
        print("replay:", await ws.recv())


if __name__ == "__main__":
    asyncio.run(main())
