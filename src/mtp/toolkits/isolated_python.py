"""Opt-in Python execution inside a caller-selected local Linux Docker image."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..protocol import ToolRiskLevel, ToolSpec
from ..runtime import RegisteredTool, ToolkitLoader
from .common import allow_ref


class ExecutionLimitError(RuntimeError):
    """The execution exceeded its wall-clock or captured-output budget."""


@dataclass(frozen=True)
class _ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def _cli_environment() -> dict[str, str]:
    # Docker needs OS paths and the user's local context configuration. None of
    # these values are forwarded into the container through docker --env.
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _bounded_run(
    argv: list[str], *, timeout: float, output_limit: int
) -> _ProcessResult:
    """Drain pipes continuously while retaining at most output_limit bytes total."""
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_cli_environment(),
        shell=False,
    )
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    lock = threading.Lock()
    total = 0

    def read_pipe(pipe: Any, index: int) -> None:
        nonlocal total
        with pipe:
            while chunk := pipe.read(4096):
                with lock:
                    remaining = output_limit - total
                    buffers[index].extend(chunk[: max(0, remaining)])
                    total += min(len(chunk), max(0, remaining))
                    if len(chunk) > remaining:
                        overflow.set()

    readers = [
        threading.Thread(target=read_pipe, args=(pipe, index), daemon=True)
        for index, pipe in enumerate((process.stdout, process.stderr))
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    error: ExecutionLimitError | None = None
    try:
        while process.poll() is None:
            if overflow.is_set():
                error = ExecutionLimitError(
                    "Execution exceeded its captured-output limit."
                )
                break
            if time.monotonic() >= deadline:
                error = ExecutionLimitError(
                    "Execution exceeded its wall-clock timeout."
                )
                break
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for reader in readers:
            reader.join(timeout=1)
    if error is not None:
        raise error
    if overflow.is_set():
        raise ExecutionLimitError("Execution exceeded its captured-output limit.")
    return _ProcessResult(process.returncode, bytes(buffers[0]), bytes(buffers[1]))


_WRAPPER = """import json, sys
scope = {"__name__": "__main__"}
exec(compile(sys.argv[1], "<mtp-container>", "exec"), scope, scope)
print("\\nMTP_RESULT=" + json.dumps(scope.get(sys.argv[2]), default=str))
"""


class DockerPythonToolkit(ToolkitLoader):
    """Run full Python with Docker restrictions; never fall back to host execution.

    The caller must provide a trusted image already present in the Linux engine.
    Construction does not start Docker, pull images, or contact the daemon.
    """

    def __init__(
        self,
        *,
        image: str,
        timeout_seconds: float = 10,
        memory_mb: int = 128,
        cpus: float = 1,
        pids_limit: int = 32,
        tmpfs_mb: int = 16,
        output_limit_bytes: int = 65536,
        source_limit_bytes: int = 16384,
        docker_executable: str | None = None,
    ) -> None:
        if not isinstance(image, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,255}", image
        ):
            raise ValueError(
                "image must be a Docker image reference without spaces or options."
            )
        for name, value, minimum, maximum in (
            ("memory_mb", memory_mb, 16, 4096),
            ("pids_limit", pids_limit, 1, 256),
            ("tmpfs_mb", tmpfs_mb, 1, 256),
            ("output_limit_bytes", output_limit_bytes, 1024, 1048576),
            ("source_limit_bytes", source_limit_bytes, 1, 16384),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(
                    f"{name} must be an integer between {minimum} and {maximum}."
                )
        for name, value, maximum in (
            ("timeout_seconds", timeout_seconds, 300),
            ("cpus", cpus, 8),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 < value <= maximum
            ):
                raise ValueError(
                    f"{name} must be finite, positive, and at most {maximum}."
                )
        self.image = image
        self.timeout_seconds = float(timeout_seconds)
        self.memory_mb = memory_mb
        self.cpus = float(cpus)
        self.pids_limit = pids_limit
        self.tmpfs_mb = tmpfs_mb
        self.output_limit_bytes = output_limit_bytes
        self.source_limit_bytes = source_limit_bytes
        self._docker_executable = docker_executable

    def _docker(self) -> str:
        executable = shutil.which(self._docker_executable or "docker")
        if executable is None:
            raise RuntimeError(
                "Docker is required; host Python execution is never used as a fallback."
            )
        return executable

    def _inspect(self, executable: str) -> str:
        info = _bounded_run(
            [executable, "info", "--format", "{{json .}}"],
            timeout=5,
            output_limit=65536,
        )
        if info.returncode:
            raise RuntimeError(
                "A running Linux Docker engine is required. No container was executed."
            )
        try:
            engine = json.loads(info.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("Docker returned invalid engine metadata.") from exc
        if not isinstance(engine, dict):
            raise TypeError("Docker returned invalid engine metadata.")
        if (
            engine.get("OSType") != "linux"
            or not engine.get("MemoryLimit")
            or not engine.get("PidsLimit")
            or not engine.get("CpuCfsQuota")
            or engine.get("CgroupDriver") in (None, "", "none")
        ):
            raise RuntimeError(
                "Docker must provide Linux memory, PID, CPU and cgroup limits."
            )
        inspected = _bounded_run(
            [executable, "image", "inspect", self.image],
            timeout=5,
            output_limit=65536,
        )
        if inspected.returncode:
            raise RuntimeError(
                "The configured image must already exist locally; MTP never pulls images."
            )
        try:
            images = json.loads(inspected.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("Docker returned invalid image metadata.") from exc
        if (
            not isinstance(images, list)
            or len(images) != 1
            or not isinstance(images[0], dict)
        ):
            raise RuntimeError("Docker returned invalid image metadata.")
        image = images[0]
        config = image.get("Config")
        if not isinstance(config, dict):
            raise TypeError("Docker returned invalid image configuration.")
        if image.get("Os") != "linux" or config.get("Volumes"):
            raise RuntimeError("Use a Linux image without declared VOLUME mounts.")
        image_id = image.get("Id", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RuntimeError("Docker returned an invalid immutable image ID.")
        return image_id

    def _run_argv(
        self, executable: str, image_id: str, name: str, code: str, variable: str
    ) -> list[str]:
        return [
            executable,
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65534:65534",
            f"--memory={self.memory_mb}m",
            f"--memory-swap={self.memory_mb}m",
            f"--cpus={self.cpus:g}",
            f"--pids-limit={self.pids_limit}",
            f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={self.tmpfs_mb}m,mode=1777",
            "--workdir=/tmp",
            "--ipc=none",
            "--log-driver=none",
            "--no-healthcheck",
            "--entrypoint=python",
            image_id,
            "-I",
            "-B",
            "-u",
            "-c",
            _WRAPPER,
            code,
            variable,
        ]

    def run_code(self, code: str, return_variable: str = "result") -> dict[str, Any]:
        if (
            not isinstance(code, str)
            or len(code.encode("utf-8")) > self.source_limit_bytes
        ):
            raise ValueError(
                "code must be a string within the configured UTF-8 source limit."
            )
        if (
            not isinstance(return_variable, str)
            or not return_variable.isidentifier()
            or len(return_variable) > 128
        ):
            raise ValueError(
                "return_variable must be a Python identifier of at most 128 characters."
            )
        executable = self._docker()
        image_id = self._inspect(executable)
        name = "mtp-python-" + uuid.uuid4().hex
        try:
            completed = _bounded_run(
                self._run_argv(executable, image_id, name, code, return_variable),
                timeout=self.timeout_seconds,
                output_limit=self.output_limit_bytes,
            )
        except BaseException:
            # Also clean up after cancellation or a keyboard interruption.
            try:
                cleanup = _bounded_run(
                    [executable, "rm", "--force", name], timeout=5, output_limit=16384
                )
            except (OSError, ExecutionLimitError) as exc:
                raise RuntimeError(
                    f"Execution failed and container cleanup failed. Remove container {name} manually."
                ) from exc
            if cleanup.returncode and b"No such container" not in cleanup.stderr:
                raise RuntimeError(
                    f"Execution failed and container cleanup failed. Remove container {name} manually."
                )
            raise
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        if completed.returncode:
            raise RuntimeError(
                f"Container Python exited with status {completed.returncode}: {stderr.strip()}"
            )
        prefix, marker, result = stdout.rpartition("\nMTP_RESULT=")
        if not marker:
            raise RuntimeError("Container Python returned no result record.")
        try:
            value = json.loads(result)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Container Python returned an invalid result record."
            ) from exc
        return {"result": value, "stdout": prefix, "stderr": stderr}

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="python_container.run_code",
                description="Run full Python in a local Linux Docker image with network disabled, no host mounts, and resource limits.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "code": allow_ref({"type": "string"}),
                        "return_variable": allow_ref({"type": "string"}),
                    },
                    "required": ["code"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.WRITE,
                side_effects="Creates and removes a resource-limited local Docker container.",
            )
        ]

    def load_tools(self) -> list[RegisteredTool]:
        return [RegisteredTool(spec=self.list_tool_specs()[0], handler=self.run_code)]
