# Modern MCP over HTTP

MTP has a separate, opt-in Streamable HTTP server for MCP revision `2026-07-28`.
Install its schema-validation dependency:

```sh
pip install "mtpx[mcp-modern]"
```

```python
from mtp.mcp import MCPJsonRpcServer
from mtp.mcp_streamable_http import MCPStreamableHTTPTransportServer
from mtp.protocol import ToolSpec
from mtp.runtime import ToolRegistry

tools = ToolRegistry()
tools.register_tool(
    ToolSpec("echo", "Return a value", {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }),
    lambda value: value,
)
server = MCPJsonRpcServer(tools=tools, enable_modern=True)
transport = MCPStreamableHTTPTransportServer("127.0.0.1", 8081, server)
transport.start()
```

`start()` blocks. Embedded applications can run it in a thread, then call
`shutdown()` from another thread. `address` contains the bound host and port
while the server runs, including an assigned port when the constructor receives
port `0`. Shutdown closes the listening socket.

## Send one request

Each POST to `/mcp` contains one JSON-RPC request, with an integer or string ID.
The transport returns one JSON response by default. This example calls the tool without an
initialization handshake:

```http
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: echo

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "echo",
    "arguments": {"value": "hello"},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/clientInfo": {"name": "example", "version": "1"}
    }
  }
}
```

The client must supply the actual `Content-Length`. The body version and
capabilities are required on every request. Client information is optional.
`server/discover` reports this endpoint's supported revision and capabilities.
Legacy `/rpc` transport and initialization behavior remain available through
`MCPHTTPTransportServer`.

## Header and credential checks

The server validates `Origin` before dispatch. By default it accepts an absent
Origin or a localhost origin with the bound port. Pass `allowed_origins` to
specify an exact allowlist. An empty set rejects all requests carrying Origin.

Configure authentication on `MCPJsonRpcServer` using `auth_token`,
`auth_validator`, or `auth_provider`. For HTTP, only `Authorization: Bearer ...`
supplies the credential. The transport removes body credentials and legacy
top-level metadata before authentication. A supplied HTTP credential overrides
all body values. It also supports asynchronous authorizers and forwards a safe
`WWW-Authenticate` challenge on authorization failure. Use a TLS reverse proxy
and an explicit Origin allowlist for remote deployments.

`Mcp-Method`, `Mcp-Name`, and the revision header must match the body. Mirrored
tool parameters annotated with `x-mcp-header` use `Mcp-Param-{name}`. MTP checks
nested property paths, primitive types, integer bounds, unique header names,
and Base64 sentinel decoding. It filters invalid annotated definitions out of
HTTP `tools/list` results and rejects calls to them before execution.

## Limits and error handling

- Missing, malformed, duplicated, or mismatched mirrored headers return HTTP
  `400` with JSON-RPC code `-32020`.
- A matching unsupported revision returns HTTP `400`, code `-32022`, and
  `requested` and `supported` versions in error data.
- Unknown RPC methods return HTTP `404` and code `-32601`.
- Authorization failures return HTTP `401`.
- JSON batches, client responses, duplicate JSON members, nonfinite numbers,
  and HTTP client notifications are rejected.
- GET and DELETE on `/mcp` return `405` with `Allow: POST`. The server ignores
  legacy session and resume headers and does not echo or create sessions.
- Bodies default to a maximum of 1 MiB and a ten-second read deadline. Configure
  `max_body_bytes` and `read_timeout_seconds` when constructing the transport.
  Chunked request bodies are unsupported. Send a single Content-Length header.

## Request-scoped SSE

Pass `sse_response=True` to enable SSE for requests carrying a progress token
and for `subscriptions/listen`. Other requests keep their JSON response. The
stream contains request-related notifications, then the final JSON-RPC reply,
and closes. It sends keepalive comments and `X-Accel-Buffering: no`, creates no
session, and provides no replay cursor.

Closing an SSE connection cancels its async handler and tool task. Concurrent
streams retain separate progress tokens and queues. Cancellation cannot forcibly
terminate a synchronous worker thread. Use the isolated toolkit or cooperative
cancellation for that case. A disconnected JSON client may leave an already
running tool executing. Configure tool deadlines separately from body reads.

The defaults allow 128 simultaneous streams, 64 queued frames per request, and
a one-hour stream lifetime. Configure `max_streams` and `max_stream_seconds`
when constructing the transport. Slow writes retain the socket timeout. Stream
shutdown cancels work, while subscriptions closed by the feature registry emit
a completion when the socket remains writable.

Install `ModernMCPFeatures` on the server to enable subscriptions and MRTR
callbacks, as described in [modern MCP](MCP_MODERN.md). JSON transports support
input-required replies, while `subscriptions/listen` requires SSE or modern
stdio. JSON-only HTTP discovery omits stream notification capability claims.

## OAuth metadata

Pass an `MCPOAuthMetadata` from `mtp.mcp_features` as `oauth_metadata`:

```python
from mtp.mcp_features import MCPOAuthMetadata

metadata = MCPOAuthMetadata(
    resource="https://mcp.example.com/mcp",
    authorization_servers=["https://identity.example.com"],
    scopes_supported=["tools:read"],
)
transport = MCPStreamableHTTPTransportServer(
    "127.0.0.1", 8081, server,
    sse_response=True, oauth_metadata=metadata,
)
```

This requires a configured server token authorizer. The server exposes Protected
Resource Metadata at `/.well-known/oauth-protected-resource/mcp` and the root
well-known alias. Unauthorized replies contain a `resource_metadata` challenge.
Origin checks apply to discovery routes too. It accepts only header credentials,
never query tokens or body tokens.

Optionally pass an explicitly trusted `authorization_server_metadata` document
whose issuer matches a configured authorization server. The adapter can expose
that document at `/.well-known/oauth-authorization-server` for a deployment's
discovery routing. It never invents an issuer, fetches untrusted metadata, issues
authorization codes or tokens, or treats a nonempty token as validated. The
configured auth provider must verify signature or introspection, issuer,
audience, expiry, and scopes against the actual identity provider. Return
`MCPAuthDecision(details={"http_status": 403}, ...)` for insufficient permission,
with the appropriate Bearer challenge. Other authorization failures use `401`.

Deploy the advertised canonical resource and metadata URLs at the proxy that
serves them. Metadata configuration alone does not establish an OAuth
authorization server or certify that external endpoints are operational.

Loopback tests exercise real HTTP requests, concurrent clients, asynchronous
tools and authorization, spoofed body credentials, mirrored headers, malformed
JSON, method rejection, and body-read deadlines.

The protocol rules come from the official [Streamable HTTP specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
and [2026-07-28 schema reference](https://modelcontextprotocol.io/specification/2026-07-28/schema).
