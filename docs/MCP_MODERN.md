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
capabilities only when configured. Optional modern features add subscriptions
and MRTR input exchanges through explicit application callbacks.

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

Cacheable complete results include `cacheScope: private` and `ttlMs: 0`. MRTR
interim results have no cache hints, and resumed resource results are immediately
stale. The official Python MCP client validates discovery, listing, tool calls,
progress, subscriptions, and input-required replies in the interoperability tests.

## Progress and subscriptions

Tool handlers can call `current_mcp_context().progress(1, total=2)` and later
`progress(2, total=2)`. Import it from `mtp.mcp_features`. Progress requires the
client's `_meta.progressToken`, increases strictly, and stays on that request's
response stream. Modern stdio writes these notifications on stdout with the
other messages. HTTP uses the opt-in SSE option described below. A cancelled
stdio request suppresses later notifications as well as its final response.

```python
from mtp.mcp_features import ModernMCPFeatures, configure_modern_mcp

features = ModernMCPFeatures()
configure_modern_mcp(server, features)
```

`subscriptions/listen` accepts the standard `notifications` filter. It first
acknowledges the honored filter, then emits only matching notification types,
with the originating subscription ID on every notification. Publish changes
explicitly when your application changes its registry or resources:

```python
features.publish("notifications/tools/list_changed", auth_token=caller_token)
features.publish(
    "notifications/resources/updated",
    {"uri": "file:///project/config.json"},
    auth_token=caller_token,
)
```

Events belong to the authenticated bearer identity and declared filter. The
library stores a credential digest, never the credential itself. Anonymous
listeners receive anonymous public events. It does not use self-reported client
names as authorization identities. A token rotation establishes a new identity.
Subscriptions end on disconnect, stdio cancellation, or `features.close()`.
Server closure sends a completion response when the stream remains writable.
There is no replay or reconnection state. Re-listen and refetch after a drop.

The defaults allow 128 subscriptions with 64 queued notifications each. Queue
overflow closes the affected subscription so clients refetch instead of assuming
they received every invalidation. Transport SSE queues hold 64 frames and the
transport also bounds simultaneous streams.

## Resumable client input

Register an interaction for a named `tools/call`, `prompts/get`, or
`resources/read` request. Its preparation callback receives parameters and the
current request context. Return an `MCPInputRequired` containing the input map
and a continuation. Other request methods cannot return input-required replies.

```python
from mtp.mcp_features import MCPInputRequired

def ask_for_roots(params, context):
    async def resume(responses, resumed_context):
        roots = responses["project_roots"]["roots"]
        # Apply application-specific permission checks to roots here.
        return await resumed_context.execute_tool()

    return MCPInputRequired(
        {"project_roots": {"method": "roots/list", "params": {}}},
        resume,
    )

features.register_interaction("tools/call", "echo", ask_for_roots)
```

The original tool runs only when the continuation calls `execute_tool()`. That
helper retains schema validation, registry policy, approval, and execution. It
accepts an optional replacement argument object. Preparation and continuation
callbacks are trusted application code. Keep preparation free of side effects;
perform tool work through the registry helper, and validate any input-specific
business permissions in the continuation. Resource and prompt callbacks return
their appropriate MCP result objects.

Supported input requests are roots, sampling, and form or URL elicitation. The
server checks the corresponding client capability before issuing an input
request. Missing support yields `-32021` and `requiredCapabilities`. It validates
received responses, including form content against the requested JSON Schema,
with external references disabled. Missing responses produce another
input-required reply containing the missing requests.

Clients retry with a different JSON-RPC ID, the exact `requestState`, and an
`inputResponses` map. The opaque random token refers to a bounded in-process
record, tied to the authenticated identity, method, salient parameters, and a
five-minute deadline. Tampered, expired, cross-user, and cross-request tokens
fail. Concurrent consumption is rejected, and completed duplicate retries
return the saved result without executing the continuation again. Different
responses after consumption fail. Failed or cancelled continuations are not
automatically retried because their side effects may already have occurred.

On an unauthenticated server, possession of the unguessable state token is the
resume credential. Configure authentication for private interactions. Client
information is never treated as proof of identity.

There are at most 256 outstanding or completed state records by default. Input
maps and saved results have a 256 KiB limit. State is process-local. It does not
survive restart or synchronize between replicas, and its exactly-once behavior
applies only while the record remains in this process. Durable applications
must use their own transactional idempotency boundary.

For HTTP use the separate [Streamable HTTP adapter](MCP_STREAMABLE_HTTP.md).
It supports JSON replies by default, optional request-scoped SSE, and explicitly
configured OAuth discovery metadata. Authorization code issuance, token
issuance, and token verification remain the configured identity provider's job.

The modern contracts follow the official [base protocol](https://modelcontextprotocol.io/specification/2026-07-28/basic/index),
[discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover),
and [stdio transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio).
