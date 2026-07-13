# Testing MTPX

MTPX uses `pytest`. The authoritative test configuration is `pytest.ini`; it
defines the `integration`, `live`, and `xiaomi` markers, enforces registered
markers, and places temporary files in `.pytest_tmp`.

## Set up a development environment

Use Python 3.10 or newer. The GitHub Actions test matrix currently exercises
Python 3.11 on Linux, Windows, and macOS.

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest pytest-asyncio
```

Install extras needed by the area you are testing. For example, transport
integration tests need the WebSocket extra:

```bash
python -m pip install -e ".[websocket]"
```

Provider tests that instantiate an optional SDK require its corresponding
extra, such as `.[groq]`, `.[openai]`, or `.[anthropic]`. `.[all]` installs all
provider, web-toolkit, and database-store dependencies, but is intentionally a
larger environment.

## Run tests

Run the complete local suite, excluding only tests that skip themselves when
credentials are absent:

```bash
python -m pytest
```

Run the fast CI suite:

```bash
python -m pytest -q -m "not integration and not live"
```

Run non-live integration tests:

```bash
python -m pytest -q -m "integration and not live"
```

Run a single module or test while iterating:

```bash
python -m pytest -q tests/test_agent.py
python -m pytest -q tests/test_agent.py::TestAgentRunLoop::test_text_only_response
```

Compile all source and test modules as an additional syntax check:

```bash
python -m compileall -q src tests
```

## Performance and memory checks

The opt-in hot-path benchmark covers incremental TUI streaming, workspace file
indexing and cached suggestions, parallel tool execution and result caching,
and bounded long-running TUI histories:

```bash
python scripts/benchmark_hot_paths.py
```

It reports elapsed time and Python peak/retained memory through `tracemalloc`.
It also reports the process RSS delta when the optional `psutil` package is
installed. The deliberately generous budgets are regression guardrails for an
ordinary development laptop, not promises about exact throughput. Run one area
by name while iterating, or collect measurements without enforcing budgets:

```bash
python scripts/benchmark_hot_paths.py tui_streaming
python scripts/benchmark_hot_paths.py --no-enforce
```

Do not compare tiny differences from a single run. Close CPU- or disk-heavy
applications and compare several runs on the same machine when investigating a
regression.

## Live provider tests

Live tests make real network requests and are never part of the default CI
jobs. Enable them deliberately and keep credentials in environment variables,
not source files or command history.

The current Xiaomi suite requires both flags below. In PowerShell:

```powershell
$env:RUN_LIVE_XIAOMI = "1"
$env:MIMO_API_KEY = "your-key"
python -m pytest -q tests/test_e2e_xiaomi.py
```

In a POSIX shell:

```bash
RUN_LIVE_XIAOMI=1 MIMO_API_KEY="your-key" \
  python -m pytest -q tests/test_e2e_xiaomi.py
```

Live tests can consume quota, encounter provider rate limits, and vary with
remote model behavior. Unit tests should mock SDK responses whenever a real
request is not essential to the behavior under test.

## Continuous integration

The workflows in `.github/workflows` run these gates on pushes to `main` or
`master` and on pull requests:

- `test-harness.yml`: fast tests on Linux, Windows, and macOS; non-live
  integration tests on Linux and Windows.
- `docs-consistency.yml`: README link and package-version consistency checks.
- `mcp-conformance.yml`: MCP security smoke tests.

Before opening a pull request, run the fast suite and the focused tests for the
code changed. Changes to transports should also run the integration selection;
changes to documentation or package metadata should run
`tests/test_docs_consistency.py`.
