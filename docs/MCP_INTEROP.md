# MCP Interoperability Adapter

This document explains how MCP is implemented in MTP today, what is fully supported, and what still remains for broader production interoperability.

## Why this exists

MTP stays the runtime/orchestration core.  
MCP stays the protocol boundary.

The adapter in `src/mtp/mcp.py` translates JSON-RPC requests into MTP runtime operations without duplicating tool execution logic.

## What is implemented now

`MCPJsonRpcServer` supports:

1. JSON-RPC 2.0 request validation and error envelopes.
2. Lifecycle:
- `initialize`
- `notifications/initialized`
3. Capability negotiation response with:
- `tools`
- `resources`
- `prompts`
- `experimental.progressNotifications`
- `experimental.requestCancellation`
4. Tool methods:
- `ping`
- `tools/list`
- `tools/call`
5. Resource methods:
- `resources/list`
- `resources/read`
6. Prompt methods:
- `prompts/list`
- `prompts/get`
7. Cancellation:
- `$/cancelRequest`
- `notifications/cancelled`
8. Progress:
- `notifications/progress` ingestion
- optional outbound progress hook during `tools/call` (when `progressToken` is provided)
9. Optional auth:
- static token (`auth_token`)
- custom validator (`auth_validator`)
10. Stdio loop helper:
- `run_mcp_stdio(server)`

## Best-effort semantics (important)

Cancellation is currently best-effort in this sync adapter:
- cancellation is checked before dispatch and before tool execution starts
- in-flight synchronous tool execution cannot be force-interrupted mid-call

Progress support is adapter-level:
- inbound progress notifications are recorded
- outbound progress events are emitted through `progress_handler` when `progressToken` is provided

## Server setup example

```python
from mtp import MCPJsonRpcServer, MCPPrompt, MCPPromptArgument, MCPResource
from mtp import ToolRegistry, ToolSpec, run_mcp_stdio

tools = ToolRegistry()
tools.register_tool(ToolSpec(name="calc.add", description="Add"), lambda a, b: a + b)

resources = [
    MCPResource(
        uri="memory://docs/quickstart",
        name="Quickstart",
        description="In-memory guide",
        mime_type="text/markdown",
    )
]

prompts = [
    MCPPrompt(
        name="explain_math",
        description="Explain arithmetic",
        arguments=[MCPPromptArgument(name="expression", required=True)],
        template="Explain this expression clearly: {expression}",
    )
]

def read_resource(uri: str) -> str:
    if uri == "memory://docs/quickstart":
        return "# Quickstart\nUse tools/list then tools/call."
    return ""

server = MCPJsonRpcServer(
    tools=tools,
    resources=resources,
    resource_reader=read_resource,
    prompts=prompts,
)
run_mcp_stdio(server)
```

Repository example:
- [mcp_stdio_server.py](/c:/Users/prajw/Downloads/MTP/examples/mcp_stdio_server.py)

## Request flow (simple)

1. `initialize`
2. `notifications/initialized`
3. Discover capabilities (`tools/list`, `resources/list`, `prompts/list`)
4. Invoke `tools/call` / `resources/read` / `prompts/get`
5. Optionally send `$/cancelRequest` before a request executes

## Method contracts

## `initialize`

Request params:
- `protocolVersion` (optional)
- `clientInfo` (optional)
- `capabilities` (optional)

Response result:
- `protocolVersion`
- `serverInfo`
- `capabilities`:
  - `tools.listChanged`
  - `resources.listChanged`
  - `prompts.listChanged`
  - `experimental.progressNotifications`
  - `experimental.requestCancellation`
- `instructions`

## `tools/list`

Response:
- `tools: [{name, description, inputSchema, annotations}]`

## `tools/call`

Request params:
- `name` (required)
- `arguments` (optional object)
- `callId` (optional)
- `progressToken` (optional; enables progress emission through `progress_handler`)

Response:
- `isError`
- `content`
- `result` (`callId`, `toolName`, `success`, `error`, `cached`, `approval`, `skipped`, `output`)

## `resources/list`

Response:
- `resources: [{uri, name, description, mimeType}]`

## `resources/read`

Request params:
- `uri` (required)

Response:
- `contents: [{uri, mimeType, text|blob}]`

## `prompts/list`

Response:
- `prompts: [{name, description, arguments}]`

## `prompts/get`

Request params:
- `name` (required)
- `arguments` (optional object)

Response:
- renderer-defined object, or default:
  - `description`
  - `messages` (single text message from template rendering)

## Cancellation notifications

Accepted notifications:
- `$/cancelRequest` with `{id}` or `{requestId}`
- `notifications/cancelled` with `{id}` or `{requestId}` or `{callId}`

Cancelled requests return JSON-RPC error code:
- `-32800` (`Request cancelled`)

## Progress notifications

- Inbound `notifications/progress` is accepted and stored in server progress history.
- Outbound progress events are emitted to `progress_handler` if `progressToken` is supplied in `tools/call`.

## Error handling

Current error codes:
- `-32700`: parse error
- `-32600`: invalid request
- `-32602`: invalid params / method usage error
- `-32000`: internal server error
- `-32001`: unauthorized
- `-32002`: server not initialized
- `-32800`: request cancelled

## Testing coverage

Tests:
- [test_mcp_adapter.py](/c:/Users/prajw/Downloads/MTP/tests/test_mcp_adapter.py)

Covered:
- lifecycle gating
- tool listing + call success/error
- auth path
- notification handling
- extended capability payload
- resources list/read
- prompts list/get
- cancellation behavior
- progress event capture

## What is still pending for full ecosystem depth

1. Streaming chunk transport specialized for MCP over HTTP/WebSocket.
2. Robust in-flight cancellation for long-running tool execution.
3. Production auth standards (OAuth discovery, scopes, refresh lifecycle).
4. External MCP client compatibility matrix and conformance suite.
