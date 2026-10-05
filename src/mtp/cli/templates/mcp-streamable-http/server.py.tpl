import os

from mtp import (
    MCPJsonRpcServer,
    MCPStreamableHTTPTransportServer,
    ToolRegistry,
    ToolSpec,
    load_dotenv_if_available,
)


def main() -> None:
    load_dotenv_if_available()
    host = os.getenv("MTP_HTTP_HOST", "127.0.0.1")
    token = os.getenv("MTP_MCP_TOKEN") or None
    if host not in {"127.0.0.1", "localhost", "::1"} and token is None:
        raise ValueError("Set MTP_MCP_TOKEN before binding a remote interface.")
    tools = ToolRegistry()
    tools.register_tool(
        ToolSpec("calc.add", "Add two numbers", input_schema={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        }),
        lambda a, b: a + b,
    )
    server = MCPJsonRpcServer(tools=tools, enable_modern=True, auth_token=token)
    MCPStreamableHTTPTransportServer(
        host, int(os.getenv("MTP_HTTP_PORT", "8081")), server,
    ).start()


if __name__ == "__main__":
    main()
