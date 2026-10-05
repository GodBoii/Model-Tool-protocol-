from __future__ import annotations

import json
import os
import sys

import pytest

from mtp.toolkits import isolated_python as isolated

IMAGE_ID = "sha256:" + "a" * 64


def _metadata_reply(argv, **kwargs):
    if "info" in argv:
        payload = {
            "OSType": "linux",
            "MemoryLimit": True,
            "PidsLimit": True,
            "CpuCfsQuota": True,
            "CgroupDriver": "systemd",
        }
    else:
        payload = [{"Id": IMAGE_ID, "Os": "linux", "Config": {"Volumes": None}}]
    return isolated._ProcessResult(0, json.dumps(payload).encode(), b"")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"image": "--privileged"},
        {"image": "python:3 --privileged"},
        {"image": ""},
        {"memory_mb": 0},
        {"memory_mb": True},
        {"pids_limit": -1},
        {"tmpfs_mb": 0},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": True},
        {"cpus": 0},
        {"cpus": 9},
        {"output_limit_bytes": 0},
        {"source_limit_bytes": 16385},
    ],
)
def test_invalid_configuration(kwargs):
    config = {"image": "python:3.13-slim", **kwargs}
    with pytest.raises(ValueError):
        isolated.DockerPythonToolkit(**config)


def test_container_flags_and_no_environment_forwarding(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-secret")
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.example:1234")
    toolkit = isolated.DockerPythonToolkit(image="python:3.13-slim")
    argv = toolkit._run_argv(
        "docker", IMAGE_ID, "mtp-python-test", "result = 42", "result"
    )
    for flag in (
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--memory=128m",
        "--memory-swap=128m",
        "--pids-limit=32",
        "--cpus=1",
        "--ipc=none",
        "--log-driver=none",
        "--entrypoint=python",
    ):
        assert flag in argv
    assert not any(arg.startswith(("--volume", "--mount", "--env")) for arg in argv)
    assert IMAGE_ID in argv
    assert "GROQ_API_KEY" not in isolated._cli_environment()
    assert "DOCKER_HOST" not in isolated._cli_environment()
    assert any(
        arg.startswith("--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m") for arg in argv
    )


def test_missing_docker_never_executes_host(monkeypatch):
    monkeypatch.setattr(isolated.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="never used as a fallback"):
        isolated.DockerPythonToolkit(image="python:3.13-slim").run_code("result = 42")


def test_inspect_requires_resource_enforcement(monkeypatch):
    def reply(argv, **kwargs):
        return isolated._ProcessResult(
            0, b'{"OSType": "linux", "MemoryLimit": false}', b""
        )

    monkeypatch.setattr(isolated, "_bounded_run", reply)
    with pytest.raises(RuntimeError, match="cgroup limits"):
        isolated.DockerPythonToolkit(image="python:3.13-slim")._inspect("docker")


@pytest.mark.parametrize(
    "metadata",
    [
        {"Os": "windows", "Config": {}, "Id": IMAGE_ID},
        {"Os": "linux", "Config": {"Volumes": {"/data": {}}}, "Id": IMAGE_ID},
        {"Os": "linux", "Config": {}, "Id": "python:latest"},
    ],
)
def test_inspect_rejects_volume_images_or_mutable_ids(monkeypatch, metadata):
    def reply(argv, **kwargs):
        if "info" in argv:
            return _metadata_reply(argv, **kwargs)
        return isolated._ProcessResult(0, json.dumps([metadata]).encode(), b"")

    monkeypatch.setattr(isolated, "_bounded_run", reply)
    with pytest.raises(RuntimeError):
        isolated.DockerPythonToolkit(image="python:3.13-slim")._inspect("docker")


def test_run_tool_and_output_record(monkeypatch):
    calls = []

    def reply(argv, **kwargs):
        calls.append(argv)
        if "run" in argv:
            return isolated._ProcessResult(0, b"hello\n\nMTP_RESULT=42\n", b"")
        return _metadata_reply(argv, **kwargs)

    monkeypatch.setattr(isolated.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(isolated, "_bounded_run", reply)
    toolkit = isolated.DockerPythonToolkit(image="python:3.13-slim")
    tool = toolkit.load_tools()[0]
    assert tool.spec.name == "python_container.run_code"
    assert tool.spec.risk_level.value == "write"
    assert tool.handler("print('hello'); result = 42") == {
        "result": 42,
        "stdout": "hello\n",
        "stderr": "",
    }
    assert calls[-1][calls[-1].index("--entrypoint=python") + 1] == IMAGE_ID


@pytest.mark.parametrize(
    "cleanup_code, cleanup_stderr", [(0, b""), (1, b"No such container: test")]
)
def test_timeout_removes_container(monkeypatch, cleanup_code, cleanup_stderr):
    calls = []

    def reply(argv, **kwargs):
        calls.append(argv)
        if "run" in argv:
            raise isolated.ExecutionLimitError("timeout")
        if "rm" in argv:
            return isolated._ProcessResult(cleanup_code, b"", cleanup_stderr)
        return _metadata_reply(argv, **kwargs)

    monkeypatch.setattr(isolated.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(isolated, "_bounded_run", reply)
    with pytest.raises(isolated.ExecutionLimitError, match="timeout"):
        isolated.DockerPythonToolkit(image="python:3.13-slim").run_code(
            "while True: pass"
        )
    name = calls[-2][calls[-2].index("--name") + 1]
    assert calls[-1] == ["docker", "rm", "--force", name]


def test_cleanup_failure_is_visible(monkeypatch):
    def reply(argv, **kwargs):
        if "run" in argv:
            raise isolated.ExecutionLimitError("timeout")
        if "rm" in argv:
            return isolated._ProcessResult(1, b"", b"daemon unavailable")
        return _metadata_reply(argv, **kwargs)

    monkeypatch.setattr(isolated.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(isolated, "_bounded_run", reply)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        isolated.DockerPythonToolkit(image="python:3.13-slim").run_code(
            "while True: pass"
        )


@pytest.mark.parametrize(
    "code, variable", [("a" * 16385, "result"), ("", "a.b"), ("", "x" * 129)]
)
def test_source_boundary_checked_before_docker(code, variable):
    with pytest.raises(ValueError):
        isolated.DockerPythonToolkit(image="python:3.13-slim").run_code(code, variable)


def test_real_process_deadline():
    with pytest.raises(isolated.ExecutionLimitError, match="wall-clock"):
        isolated._bounded_run(
            [sys.executable, "-I", "-c", "import time; time.sleep(10)"],
            timeout=0.1,
            output_limit=1024,
        )


def test_real_process_output_limit():
    with pytest.raises(isolated.ExecutionLimitError, match="captured-output"):
        isolated._bounded_run(
            [sys.executable, "-I", "-c", "import sys; sys.stdout.write('x' * 100000)"],
            timeout=3,
            output_limit=1024,
        )


def test_real_process_environment_has_no_provider_secret(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-secret")
    result = isolated._bounded_run(
        [
            sys.executable,
            "-I",
            "-c",
            "import os; print(os.environ.get('GROQ_API_KEY'))",
        ],
        timeout=3,
        output_limit=1024,
    )
    assert result.stdout.strip() == b"None"


def test_real_wrapper_serializes_result_without_printing_source():
    result = isolated._bounded_run(
        [
            sys.executable,
            "-I",
            "-B",
            "-u",
            "-c",
            isolated._WRAPPER,
            "print('hello'); result = sum(range(10))",
            "result",
        ],
        timeout=3,
        output_limit=1024,
    )
    assert result.returncode == 0
    assert result.stdout.replace(b"\r\n", b"\n") == b"hello\n\nMTP_RESULT=45\n"
    assert result.stderr == b""


def test_interruption_also_removes_container(monkeypatch):
    calls = []

    def reply(argv, **kwargs):
        calls.append(argv)
        if "run" in argv:
            raise KeyboardInterrupt
        if "rm" in argv:
            return isolated._ProcessResult(0, b"", b"")
        return _metadata_reply(argv, **kwargs)

    monkeypatch.setattr(isolated.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(isolated, "_bounded_run", reply)
    with pytest.raises(KeyboardInterrupt):
        isolated.DockerPythonToolkit(image="python:3.13-slim").run_code("result = 42")
    assert "rm" in calls[-1]


@pytest.mark.parametrize("payload", [b"not json", b"[]"])
def test_invalid_engine_metadata_is_rejected(monkeypatch, payload):
    monkeypatch.setattr(
        isolated,
        "_bounded_run",
        lambda *args, **kwargs: isolated._ProcessResult(0, payload, b""),
    )
    with pytest.raises((RuntimeError, TypeError), match="invalid engine metadata"):
        isolated.DockerPythonToolkit(image="python:3.13-slim")._inspect("docker")


@pytest.mark.integration
def test_opt_in_real_linux_container(monkeypatch):
    image = os.environ.get("MTP_DOCKER_TEST_IMAGE")
    if not image:
        pytest.skip(
            "Set MTP_DOCKER_TEST_IMAGE to an already installed trusted Linux Python image."
        )
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-host-key")
    toolkit = isolated.DockerPythonToolkit(image=image, timeout_seconds=10)
    result = toolkit.run_code("""
import os
from pathlib import Path

base = Path('/sys/fs/cgroup')
if (base / 'cgroup.controllers').exists():
    memory = int((base / 'memory.max').read_text())
    pids = int((base / 'pids.max').read_text())
    quota, period = (base / 'cpu.max').read_text().split()
else:
    memory = int((base / 'memory/memory.limit_in_bytes').read_text())
    pids = int((base / 'pids/pids.max').read_text())
    cpu = next(p for p in (base / 'cpu', base / 'cpu,cpuacct')
               if (p / 'cpu.cfs_quota_us').exists())
    quota = (cpu / 'cpu.cfs_quota_us').read_text()
    period = (cpu / 'cpu.cfs_period_us').read_text()
tmp = Path('/tmp/mtp-test')
tmp.write_text('container-only')
tmp_mount = next(line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()
                 if line.split()[4] == '/tmp')
result = {
    'uid': os.getuid(), 'gid': os.getgid(), 'secret': os.getenv('GROQ_API_KEY'),
    'interfaces': sorted(os.listdir('/sys/class/net')),
    'root_readonly': bool(os.statvfs('/').f_flag & os.ST_RDONLY),
    'tmp': tmp.read_text(), 'tmp_mount_options': tmp_mount[5].split(','),
    'tmp_filesystem': tmp_mount[tmp_mount.index('-') + 1],
    'tmp_bytes': os.statvfs('/tmp').f_blocks * os.statvfs('/tmp').f_frsize,
    'memory': memory, 'pids': pids, 'cpus': int(quota) / int(period),
}
""")
    observed = result["result"]
    assert observed["uid"] == observed["gid"] == 65534
    assert observed["secret"] is None
    assert observed["interfaces"] == ["lo"]
    assert observed["root_readonly"] is True
    assert observed["tmp"] == "container-only"
    assert observed["tmp_filesystem"] == "tmpfs"
    assert observed["tmp_bytes"] == 16 * 1024 * 1024
    assert {"rw", "noexec", "nosuid", "nodev"} <= set(observed["tmp_mount_options"])
    assert observed["memory"] == 128 * 1024 * 1024
    assert observed["pids"] == 32
    assert observed["cpus"] == pytest.approx(1)
    with pytest.raises(RuntimeError):
        toolkit.run_code("open('/mtp-forbidden', 'w').write('bad')")
    launched_names = []
    original_run = isolated._bounded_run

    def recording_run(argv, **kwargs):
        if "run" in argv:
            launched_names.append(argv[argv.index("--name") + 1])
        return original_run(argv, **kwargs)

    monkeypatch.setattr(isolated, "_bounded_run", recording_run)
    with pytest.raises(isolated.ExecutionLimitError):
        isolated.DockerPythonToolkit(image=image, timeout_seconds=0.5).run_code(
            "while True: pass"
        )
    inspected = original_run(
        [toolkit._docker(), "container", "inspect", launched_names[-1]],
        timeout=5,
        output_limit=16384,
    )
    assert inspected.returncode != 0
    assert b"No such" in inspected.stderr
