# Modern MCP core and stdio

Install `pip install "mtpx[mcp-modern]"` and pass `enable_modern=True` when
constructing `MCPJsonRpcServer`. The default remains the legacy initialization
lifecycle. Modern requests carry metadata on every call, so one client's
discovery or identity does not initialize another client's request.

```python
from mtp import MCPJsonRpcServer, ToolRegistry, run_mcp_stdio

server = MCPJsonRpcServer(tools=ToolRegistry(), enable_modern=True)
run_mcp_stdio(server)
```

Send one UTF-8 JSON object per line. A discovery request is:

```json
{
  "jsonrpc": "2.0",
  "id": "discover-1",
  "method": "server/discover",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

`clientInfo` is optional and informational. The revision and capabilities are
required on every request. Missing required metadata yields `-32602`.
An unsupported modern revision yields `-32022` with retry versions and the
requested revision. Replies include `resultType: complete` and server identity
in result metadata. Unknown methods yield `-32601`. Discovery lists the core's
modern and legacy revisions; the modern HTTP endpoint lists only its own
revision.

The core implements discovery, ping, tool listing/calling, resource
listing/reading, and prompt listing/rendering. It advertises resource and prompt
capabilities only when configured. It does not initiate client requests or
advertise subscriptions, MRTR input exchanges, progress streaming, or extensions.

Modern tool arguments use JSON Schema 2020-12 validation. Other dialects fail
clearly. External schema references never trigger network or file reads. Local
`$defs` and fragment references work. Schema traversal is limited to depth 64
and 10,000 nodes. Modern arguments pass through the registry's ordinary policy,
approval, caching, and execution path. They retain literal JSON fields rather
than MTP result references or scalar coercion. Legacy and agent calls preserve
their existing MTP reference behavior.

Modern stdio requests run concurrently with a maximum of 256 in flight. Send
`notifications/cancelled` with `params.requestId` to cancel an in-flight request.
The server suppresses further replies for that request and cancels async work.
Unknown cancellation IDs do not poison future reuse. String and integer IDs are
distinct. Closing stdin cancels pending requests and shuts down the reader.
Synchronous tool workers cannot be forcibly terminated; use cooperative
cancellation or the separate isolated toolkit for bounded execution.

Direct `handle_request` calls are synchronous and cannot process a second
message while blocked. Use `ahandle_request` in async integrations, and
`run_mcp_stdio` for concurrent modern stdin processing. Legacy clients can still
initialize and use the same opted-in server under their serial lifecycle.

For HTTP use the separate [Streamable HTTP adapter](MCP_STREAMABLE_HTTP.md).
Its JSON-only reply option has no disconnect cancellation or progress stream.
This package does not claim OAuth discovery or complete implementation of every
optional MCP feature.

The modern contracts follow the official [base protocol](https://modelcontextprotocol.io/specification/2026-07-28/basic/index),
[discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover),
and [stdio transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio).
