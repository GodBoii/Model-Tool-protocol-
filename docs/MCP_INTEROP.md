# MCP Interoperability Adapter

This document explains MCP support in MTP, including sync and async server modes, dedicated MCP HTTP/WebSocket transport adapters, cancellation semantics, and compatibility coverage.

## Overview

MTP keeps orchestration/runtime logic in core modules.
MCP support is provided by protocol adapters around that runtime.

Main components:

- `MCPJsonRpcServer` in `src/mtp/mcp.py`
- `MCPHTTPTransportServer` in `src/mtp/mcp_transport.py`
- `MCPWebSocketTransportServer` in `src/mtp/mcp_transport.py`

## Implemented MCP method coverage

Lifecycle:
- `initialize`
- `notifications/initialized`

Core:
- `ping`
- `tools/list`
- `tools/call`

Resources:
- `resources/list`
- `resources/read`

Prompts:
- `prompts/list`
- `prompts/get`

Progress/Cancellation:
- `notifications/progress`
- `$/cancelRequest`
- `notifications/cancelled`

## Capability negotiation

`initialize` returns:

- `tools.listChanged`
- `resources.listChanged`
- `prompts.listChanged`
- `experimental.progressNotifications`
- `experimental.requestCancellation`

## OAuth-ready auth plugin interface

`MCPJsonRpcServer` now supports pluggable auth providers:

- constructor arg: `auth_provider=...`
- provider contract: `authorize(token, request, context) -> MCPAuthDecision | bool`
- async providers are supported in async path (`ahandle_request`)

`MCPAuthDecision` fields:

- `allowed`
- `error_code` (default `-32001`)
- `message`
- `www_authenticate` (for challenge headers)
- `details` (structured auth metadata)

This enables OAuth-style challenge responses without hard-coding one auth backend into core MCP code.

Example:

```python
from mtp import MCPAuthDecision, MCPJsonRpcServer

class OAuthProvider:
    def authorize(self, token, request, context):
        if token == "valid-token":
            return MCPAuthDecision(allowed=True)
        return MCPAuthDecision(
            allowed=False,
            message="Missing OAuth bearer token",
            www_authenticate='Bearer realm="mtp", error="invalid_token"',
        )

server = MCPJsonRpcServer(tools=tools, auth_provider=OAuthProvider())
```

## Sync vs async request handling

`MCPJsonRpcServer` supports:

- sync:
  - `handle_request(...)`
  - `handle_json(...)`
- async:
  - `ahandle_request(...)`
  - `ahandle_json(...)`

Use async handlers when you need stronger in-flight cancellation for long-running async tool calls.

## In-flight cancellation semantics

Cancellation model:

1. Client sends `$/cancelRequest` or `notifications/cancelled`.
2. Request id is marked as cancelled.
3. Async call tasks (if active) are cancelled immediately.
4. Cancelled requests return JSON-RPC error `-32800`.

Important:

- Async tool calls support true in-flight cancellation.
- Synchronous tool handlers are still cooperative at runtime level (`cancel_event`/`cancel_checker`).

## Progress semantics

- inbound `notifications/progress` is accepted and recorded
- outbound progress events are emitted from `tools/call` when `progressToken` is set
- progress events are available via:
  - `MCPJsonRpcServer.progress_events`
  - `progress_handler`
  - registered progress listeners (used by MCP HTTP/WebSocket transport)

## MCP-specific HTTP transport

`MCPHTTPTransportServer(host, port, server)` adds MCP-aware HTTP behavior:

- POST JSON-RPC endpoint: `/rpc` (also `/`)
- batch JSON-RPC request support (array payload)
- session header propagation:
  - request/response header: `MCP-Session-Id`
- bearer token propagation:
  - `Authorization: Bearer <token>` -> `params.auth_token`
- auth challenge propagation:
  - JSON-RPC auth error `error.data.www_authenticate` -> HTTP `WWW-Authenticate` header
- progress event polling endpoint:
  - `GET /events?limit=20`

Example:

```python
from mtp import MCPHTTPTransportServer, MCPJsonRpcServer, ToolRegistry, ToolSpec

tools = ToolRegistry()
tools.register_tool(ToolSpec(name="calc.add", description="Add"), lambda a, b: a + b)

server = MCPJsonRpcServer(tools=tools)
transport = MCPHTTPTransportServer("127.0.0.1", 8081, server)
transport.start()
```

Repository example:
- [mcp_http_server.py](/c:/Users/prajw/Downloads/MTP/examples/mcp_http_server.py)

## MCP-specific WebSocket transport

`MCPWebSocketTransportServer(host, port, server)`:

- receives JSON-RPC requests over websocket
- uses async request handling (`ahandle_request`)
- sends standard JSON-RPC responses
- broadcasts progress as JSON-RPC notifications:
  - `method: "notifications/progress"`

Example:

```python
from mtp import MCPWebSocketTransportServer, MCPJsonRpcServer

ws_server = MCPWebSocketTransportServer("127.0.0.1", 8766, MCPJsonRpcServer(tools=tools))
await ws_server.start()
await ws_server.serve_forever()
```

## Error model

Returned error codes:

- `-32700`: parse error
- `-32600`: invalid request
- `-32602`: invalid params
- `-32000`: internal server error
- `-32001`: unauthorized
- `-32002`: server not initialized
- `-32800`: request cancelled

## Compatibility/conformance matrix

Current matrix in this repo:

- JSON-RPC request validation: covered
- initialize lifecycle gate: covered
- tools list/call mapping: covered
- resources list/read: covered
- prompts list/get: covered
- auth denial path: covered
- pluggable auth provider path (sync + async): covered
- OAuth challenge metadata path (`www_authenticate`): covered
- cancellation handling: covered
- progress event capture: covered
- async in-flight cancellation: covered
- MCP HTTP adapter semantics (session/auth headers, batch, events): covered

## Test coverage

- [test_mcp_adapter.py](/c:/Users/prajw/Downloads/MTP/tests/test_mcp_adapter.py)
- [test_mcp_transport.py](/c:/Users/prajw/Downloads/MTP/tests/test_mcp_transport.py)

## Remaining MCP work

1. OAuth discovery/scope/refresh integrations beyond provider hook level.
2. External client interoperability matrix against third-party MCP clients.
3. Optional SSE/streaming endpoint variants for broader client preference beyond current `/events` polling and websocket notifications.
