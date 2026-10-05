# Python execution isolation

`DockerPythonToolkit` is an opt-in toolkit for full Python execution in a
caller-selected local Linux Docker image. It exposes
`python_container.run_code`. It does not replace `PythonToolkit` or register
automatically in CLI or TUI sessions.

```python
from mtp.runtime import ToolRegistry
from mtp.toolkits.isolated_python import DockerPythonToolkit

registry = ToolRegistry()
registry.register_toolkit_loader(
    "python_container",
    DockerPythonToolkit(image="python:3.13-slim", timeout_seconds=10),
)
```

The image must already exist in the selected local Docker engine. MTP uses
`--pull=never`, resolves the image to its immutable image ID, and rejects images
that declare `VOLUME` mounts. MTP does not install Docker, start its daemon,
download an image, or change operating-system security settings. The image must
be trusted and must provide a `python` executable usable by UID 65534.

Each call starts a fresh container with:

- Network disabled through `--network=none`.
- No host directory, Docker socket, environment file, or credential mount.
- A read-only root filesystem and a 16 MiB writable `/tmp` tmpfs by default.
- UID and GID 65534, all Linux capabilities dropped, and no new privileges.
- Default limits of 128 MiB memory with no additional swap, one CPU, and 32 PIDs.
- Private IPC, disabled image health checks, and disabled Docker logging.
- A ten-second execution deadline and at most 65,536 captured output bytes.

MTP checks that the engine reports Linux CPU, memory, PID, and cgroup limit
support before running code. Engines without those capabilities fail closed.
The Docker CLI receives only selected operating-system path and local context
variables. MTP does not forward host API keys into the container. Environment
values baked into the caller's image remain part of that trusted image.

The handler accepts `code` and an optional `return_variable`, defaulting to
`result`. It returns `{"result": ..., "stdout": ..., "stderr": ...}`. Non-JSON
return values serialize using `str`. Source is limited to 16,384 UTF-8 bytes.
Container exit failures, oversized output, timeouts, and malformed result
records raise errors. The tool has write risk and participates in the ordinary
registry approval policy.

`timeout_seconds`, `memory_mb`, `cpus`, `pids_limit`, `tmpfs_mb`,
`output_limit_bytes`, and `source_limit_bytes` can be set explicitly within
validated upper limits. The execution timeout starts after engine and image
checks, which each have a separate five-second deadline. Failure cleanup can
take another five seconds. Normal completion uses Docker's `--rm`. On a timeout,
output limit, or interruption MTP also attempts `docker rm --force` using the
unique container name. A failed cleanup identifies the name for manual removal.
Docker daemon outages or an interrupted creation request can leave cleanup
unconfirmed. This requires an operational check on the daemon; MTP cannot
guarantee removal while the daemon is unavailable.

Docker containers share the engine's kernel. This is a container boundary with
specific restrictions, not a promise that hostile code cannot exploit the
kernel or container runtime. Use a maintained engine and a trusted image. Do not
place credentials in source code, which is passed to the Docker CLI as an
argument, or in the selected image. The toolkit has no host workspace access,
package installation, external network access, persistent interpreter state,
or `run_file` handler.

`PythonToolkit` remains a subprocess executor with arithmetic and collection
restrictions by default. Its `allow_unsafe_exec=True` mode executes host Python
with the host user's permissions. Neither mode is an operating-system sandbox.
Selecting a working directory does not isolate the host filesystem.

## Verification

Tests cover container flags, rejected configuration, immutable image selection,
volume rejection, engine capability checks, approval metadata, timeout cleanup,
visible cleanup failures, source limits, and result handling. Real local Python
children exercise deadline enforcement, output bounds, and removal of a
synthetic provider key from the subprocess environment.

The Docker executable exists on the audit host, but its Linux daemon is stopped.
Container execution and its kernel-enforced restrictions remain unverified on
this host. A real Docker test is opt-in and skips by default. To run it against
an image you have already installed, set `MTP_DOCKER_TEST_IMAGE` to that image
reference and run `pytest tests/test_isolated_python.py`. The test checks the
non-root UID and GID, absent synthetic host key, loopback-only network interfaces,
read-only root filesystem, writable tmpfs and mount restrictions, and actual
memory, PID, and CPU cgroup limits. It also exercises timeout and cleanup. Once
the environment variable is set, unavailable or unsupported Docker engines fail
the test instead of skipping it. The Linux Docker CI job installs its trusted
test image explicitly before invoking this test; the toolkit still never pulls
an image itself.

The flags and capability assumptions follow Docker's
[container run reference](https://docs.docker.com/reference/cli/docker/container/run/),
[none network driver](https://docs.docker.com/engine/network/drivers/none/), and
[rootless resource-limit requirements](https://docs.docker.com/engine/security/rootless/tips/).
