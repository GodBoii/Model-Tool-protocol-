from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.protocol import ToolCall
from mtp.runtime import ToolRegistry
from mtp.toolkits.local import register_local_toolkits


class LocalToolkitsTests(unittest.TestCase):
    def test_list_tools_includes_lazy_specs(self) -> None:
        reg = ToolRegistry()
        register_local_toolkits(registry=reg, base_dir=pathlib.Path.cwd())
        tool_names = {tool.name for tool in reg.list_tools()}
        self.assertIn("calculator.add", tool_names)
        self.assertIn("file.read_file", tool_names)
        self.assertIn("python.run_code", tool_names)
        self.assertIn("shell.run_command", tool_names)

    def test_calculator_and_file_tools_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = ToolRegistry()
            register_local_toolkits(registry=reg, base_dir=tmp)

            add_result = asyncio.run(
                reg.execute_call(
                    ToolCall(id="c1", name="calculator.add", arguments={"a": 20, "b": 22}),
                    {},
                )
            )
            self.assertEqual(add_result.output, 42)

            write_result = asyncio.run(
                reg.execute_call(
                    ToolCall(
                        id="f1",
                        name="file.write_file",
                        arguments={"path": "demo.txt", "content": "hello"},
                    ),
                    {},
                )
            )
            self.assertTrue(write_result.success)

            read_result = asyncio.run(
                reg.execute_call(
                    ToolCall(id="f2", name="file.read_file", arguments={"path": "demo.txt"}),
                    {},
                )
            )
            self.assertEqual(read_result.output, "hello")

            shell_result = asyncio.run(
                reg.execute_call(
                    ToolCall(id="s1", name="shell.run_command", arguments={"command": "python --version"}),
                    {},
                )
            )
            self.assertFalse(shell_result.success)
            self.assertIn("not allowed", shell_result.error or "")


if __name__ == "__main__":
    unittest.main()
