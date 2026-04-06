import os

from mtp import MCPHTTPTransportServer, MCPJsonRpcServer, ToolRegistry, ToolSpec


def main() -> None:
    tools = ToolRegistry()
    tools.register_tool(ToolSpec(name="calc.add", description="Add two numbers"), lambda a, b: a + b)
    tools.register_tool(ToolSpec(name="calc.mul", description="Multiply two numbers"), lambda a, b: a * b)

    server = MCPJsonRpcServer(
        tools=tools,
        instructions="MCP HTTP scaffold server. POST JSON-RPC to /rpc and stream /events/sse.",
    )
    host = os.getenv("MTP_HTTP_HOST", "127.0.0.1")
    port = int(os.getenv("MTP_HTTP_PORT", "8081"))
    transport = MCPHTTPTransportServer(host, port, server)
    transport.start()


if __name__ == "__main__":
    main()

