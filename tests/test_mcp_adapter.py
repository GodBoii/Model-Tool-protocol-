from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import (
    MCPJsonRpcServer,
    MCPPrompt,
    MCPPromptArgument,
    MCPResource,
    MCPServerInfo,
    ToolRegistry,
    ToolSpec,
)


class MCPAdapterTests(unittest.TestCase):
    def _new_registry(self) -> ToolRegistry:
        reg = ToolRegistry()
        reg.register_tool(ToolSpec(name="calc.add", description="add"), lambda a, b: a + b)
        reg.register_tool(ToolSpec(name="ops.fail", description="fail"), lambda: 1 / 0)
        return reg

    def test_rejects_non_initialized_tool_methods(self) -> None:
        server = MCPJsonRpcServer(tools=self._new_registry())
        response = server.handle_request(
            {"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}}
        )
        assert response is not None
        self.assertEqual(response["error"]["code"], -32002)
        self.assertIn("not initialized", response["error"]["message"].lower())

    def test_initialize_then_list_tools(self) -> None:
        server = MCPJsonRpcServer(
            tools=self._new_registry(),
            server_info=MCPServerInfo(name="mtp-test", version="0.0.1"),
        )
        init = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "init-1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2026-03-26",
                    "clientInfo": {"name": "tester"},
                    "capabilities": {"roots": {}},
                },
            }
        )
        assert init is not None
        self.assertEqual(init["result"]["serverInfo"]["name"], "mtp-test")
        self.assertTrue(server.initialized)
        self.assertEqual(server.client_info.get("name"), "tester")

        listed = server.handle_request(
            {"jsonrpc": "2.0", "id": "tools-1", "method": "tools/list", "params": {}}
        )
        assert listed is not None
        tools = listed["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertIn("calc.add", names)
        self.assertIn("ops.fail", names)

    def test_tools_call_success_and_error_shapes(self) -> None:
        server = MCPJsonRpcServer(tools=self._new_registry())
        server.handle_request({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})

        ok = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "call-1",
                "method": "tools/call",
                "params": {"name": "calc.add", "arguments": {"a": 2, "b": 3}},
            }
        )
        assert ok is not None
        self.assertFalse(ok["result"]["isError"])
        self.assertEqual(ok["result"]["result"]["output"], 5)

        bad = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "call-2",
                "method": "tools/call",
                "params": {"name": "ops.fail", "arguments": {}},
            }
        )
        assert bad is not None
        self.assertTrue(bad["result"]["isError"])
        self.assertIn("division by zero", bad["result"]["content"][0]["text"])

    def test_authentication_denies_without_token(self) -> None:
        server = MCPJsonRpcServer(tools=self._new_registry(), auth_token="secret", require_auth=True)

        denied = server.handle_request(
            {"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}}
        )
        assert denied is not None
        self.assertEqual(denied["error"]["code"], -32001)

        allowed = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "init-2",
                "method": "initialize",
                "params": {"auth_token": "secret"},
            }
        )
        assert allowed is not None
        self.assertIn("result", allowed)

    def test_notifications_return_none(self) -> None:
        server = MCPJsonRpcServer(tools=self._new_registry())
        server.handle_request({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})
        response = server.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        self.assertIsNone(response)
        self.assertTrue(server.client_initialized)

    def test_initialize_includes_extended_capabilities(self) -> None:
        server = MCPJsonRpcServer(tools=self._new_registry())
        init = server.handle_request({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})
        assert init is not None
        caps = init["result"]["capabilities"]
        self.assertIn("tools", caps)
        self.assertIn("resources", caps)
        self.assertIn("prompts", caps)
        self.assertTrue(caps["experimental"]["progressNotifications"])
        self.assertTrue(caps["experimental"]["requestCancellation"])

    def test_resources_list_and_read(self) -> None:
        resources = [
            MCPResource(
                uri="memory://readme",
                name="README",
                description="In-memory readme",
                mime_type="text/markdown",
            )
        ]
        server = MCPJsonRpcServer(
            tools=self._new_registry(),
            resources=resources,
            resource_reader=lambda uri: "# Hello MCP" if uri == "memory://readme" else "",
        )
        server.handle_request({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})

        listed = server.handle_request(
            {"jsonrpc": "2.0", "id": "r-list", "method": "resources/list", "params": {}}
        )
        assert listed is not None
        self.assertEqual(listed["result"]["resources"][0]["uri"], "memory://readme")

        read = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "r-read",
                "method": "resources/read",
                "params": {"uri": "memory://readme"},
            }
        )
        assert read is not None
        self.assertEqual(read["result"]["contents"][0]["text"], "# Hello MCP")

    def test_prompts_list_and_get_with_template(self) -> None:
        prompts = [
            MCPPrompt(
                name="summarize",
                description="Summarize input",
                arguments=[MCPPromptArgument(name="topic", required=True)],
                template="Summarize this topic: {topic}",
            )
        ]
        server = MCPJsonRpcServer(tools=self._new_registry(), prompts=prompts)
        server.handle_request({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})

        listed = server.handle_request(
            {"jsonrpc": "2.0", "id": "p-list", "method": "prompts/list", "params": {}}
        )
        assert listed is not None
        self.assertEqual(listed["result"]["prompts"][0]["name"], "summarize")

        got = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "p-get",
                "method": "prompts/get",
                "params": {"name": "summarize", "arguments": {"topic": "MTP"}},
            }
        )
        assert got is not None
        text = got["result"]["messages"][0]["content"]["text"]
        self.assertIn("Summarize this topic: MTP", text)

    def test_cancelled_request_returns_cancel_error(self) -> None:
        server = MCPJsonRpcServer(tools=self._new_registry())
        server.handle_request({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})

        server.handle_request(
            {
                "jsonrpc": "2.0",
                "method": "$/cancelRequest",
                "params": {"id": "call-cancel"},
            }
        )

        cancelled = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "call-cancel",
                "method": "tools/call",
                "params": {"name": "calc.add", "arguments": {"a": 1, "b": 2}},
            }
        )
        assert cancelled is not None
        self.assertEqual(cancelled["error"]["code"], -32800)
        self.assertIn("cancelled", cancelled["error"]["message"].lower())

    def test_progress_events_collected_for_tool_call(self) -> None:
        server = MCPJsonRpcServer(tools=self._new_registry())
        server.handle_request({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})

        server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "progress-call",
                "method": "tools/call",
                "params": {
                    "name": "calc.add",
                    "arguments": {"a": 3, "b": 4},
                    "progressToken": "tok-1",
                },
            }
        )
        outbound = [e for e in server.progress_events if e.get("direction") == "outbound"]
        self.assertGreaterEqual(len(outbound), 2)
        self.assertEqual(outbound[0]["progress"], 0)
        self.assertEqual(outbound[-1]["progress"], 1)


if __name__ == "__main__":
    unittest.main()


class MCPAdapterAsyncTests(unittest.IsolatedAsyncioTestCase):
    def _new_registry(self) -> ToolRegistry:
        reg = ToolRegistry()

        async def slow_add(a: int, b: int, cancel_checker=None) -> int:
            for _ in range(200):
                if callable(cancel_checker) and cancel_checker():
                    await asyncio.sleep(0)
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.01)
            return a + b

        reg.register_tool(ToolSpec(name="calc.slow_add", description="slow add"), slow_add)
        return reg

    async def test_async_tools_call_can_be_cancelled_in_flight(self) -> None:
        server = MCPJsonRpcServer(tools=self._new_registry())
        await server.ahandle_request({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})

        call_task = asyncio.create_task(
            server.ahandle_request(
                {
                    "jsonrpc": "2.0",
                    "id": "req-1",
                    "method": "tools/call",
                    "params": {
                        "name": "calc.slow_add",
                        "arguments": {"a": 1, "b": 2},
                        "callId": "call-1",
                    },
                }
            )
        )

        await asyncio.sleep(0.05)
        await server.ahandle_request(
            {
                "jsonrpc": "2.0",
                "method": "$/cancelRequest",
                "params": {"id": "req-1"},
            }
        )
        response = await call_task
        assert response is not None
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32800)
