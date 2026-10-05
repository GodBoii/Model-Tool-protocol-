# {{PROJECT_NAME}}

An MCP 2026-07-28 server with stateless JSON replies at `POST /mcp`.

Create a virtual environment, install this project with `pip install -e .`,
then run `mtp run`. It exposes the `calc.add` tool. The generated `.env.example`
lists the binding options. Copy it to `.env` when configuring the server.

Clients supply JSON content type, Accept for both JSON and SSE, the revision
header, `Mcp-Method`, and `Mcp-Name` for tool calls. Every request includes
`params._meta` with `io.modelcontextprotocol/protocolVersion` and
`io.modelcontextprotocol/clientCapabilities`. No initialization is needed.

`MTP_MCP_TOKEN` enables bearer authentication. Remote binding requires a token.
Use a TLS reverse proxy and an explicit Origin allowlist for remote deployment.
The transport supports JSON responses only. SSE, subscriptions, and MRTR are
not implemented. This template requires mtpx 0.1.40 or a local checkout with
these changes until that version is published.
