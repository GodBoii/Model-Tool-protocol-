from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from ..protocol import ToolRiskLevel, ToolSpec
from ..runtime import RegisteredTool, ToolkitLoader
from .common import allow_ref


class ShellToolkit(ToolkitLoader):
    def __init__(
        self,
        base_dir: str | Path | None = None,
        timeout_seconds: int = 20,
        *,
        allowed_commands: set[str] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir or Path.cwd()).resolve()
        self.timeout_seconds = timeout_seconds
        self.allowed_commands = {cmd.lower() for cmd in (allowed_commands or {"echo", "pwd", "ls", "dir"})}

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="shell.run_command",
                description="Run a shell command in base_dir and return stdout/stderr.",
                input_schema={
                    "type": "object",
                    "properties": {"command": allow_ref({"type": "string"})},
                    "required": ["command"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.WRITE,
            )
        ]

    def load_tools(self) -> list[RegisteredTool]:
        def run_command(command: str) -> dict[str, Any]:
            command_parts = shlex.split(command, posix=(os.name != "nt"))
            if not command_parts:
                raise ValueError("Empty command.")
            command_name = Path(command_parts[0]).name.lower()
            if command_name not in self.allowed_commands:
                raise ValueError(
                    f"Command '{command_name}' is not allowed. Allowed: {sorted(self.allowed_commands)}"
                )
            completed = subprocess.run(
                command_parts,
                shell=False,
                cwd=str(self.base_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            return {
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }

        return [
            RegisteredTool(
                spec=self.list_tool_specs()[0],
                handler=run_command,
            )
        ]
