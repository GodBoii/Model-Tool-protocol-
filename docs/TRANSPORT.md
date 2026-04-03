# Transport Layer (v0.1)

MTP now includes envelope transport primitives:
- stdio transport
- HTTP transport

Both use `MessageEnvelope` JSON serialization.

## Envelope model

`MessageEnvelope` fields:
- `mtp_version`
- `kind`
- `payload`
- `metadata`

Helpers:
- `to_dict()` / `from_dict()`
- `to_json()` / `from_json()`

## stdio transport

`run_stdio_transport(handler)`:
- reads one JSON envelope per stdin line
- writes one JSON envelope per stdout line

```python
from mtp.schema import MessageEnvelope
from mtp.transport import run_stdio_transport

def handler(env: MessageEnvelope) -> MessageEnvelope:
    return MessageEnvelope.create(kind="pong", payload={"echo": env.payload})

run_stdio_transport(handler)
```

## HTTP transport

`HTTPTransportServer(host, port, handler)`:
- accepts POST requests with JSON envelope
- responds with JSON envelope

```python
from mtp.schema import MessageEnvelope
from mtp.transport import HTTPTransportServer

def handler(env: MessageEnvelope) -> MessageEnvelope:
    return MessageEnvelope.create(kind="ok", payload={"kind": env.kind})

server = HTTPTransportServer("127.0.0.1", 8080, handler)
server.start()
```

## Notes

- This is intentionally minimal transport scaffolding for next-phase expansion.
- Authentication, retries, and distributed tracing are planned.

Related:
- [Storage and Sessions](C:\Users\prajw\Downloads\MTP\docs\STORAGE.md)
