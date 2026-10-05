# Implementation phases 3 through 5

Completed on 2026-10-05 using three parallel implementation agents, with core
integration and verification in the main chat. Version 0.1.40 is prepared for
publication. Source changes are committed and pushed to `main`.

## Modern MCP

- Explicit `enable_modern=True` adds stateless 2026-07-28 requests, discovery,
  per-request capabilities, server identity, and complete-result markers.
- The separate `/mcp` transport returns JSON and validates Origin, mirrored
  headers, credentials, input sizes, and body-read deadlines. Only HTTP bearer
  credentials reach the authorizer. It does not create or echo sessions.
- Modern stdio supports concurrent requests, in-flight cancellation, request ID
  reuse after completion, and shutdown on EOF.
- JSON Schema 2020-12 validates raw tool arguments without MTP coercion or
  result-reference substitution. The ordinary policy and approval checks still
  apply. External schema fetches are disabled.
- `mtp new NAME --template mcp-streamable-http` generates a server, dependency
  metadata, README, and environment template. Remote binding requires a token.

Legacy initialization, `/rpc`, event replay, and existing agent calls retain
their prior behavior. Install the optional `mcp-modern` extra to enable the new
core. See [modern core/stdio](MCP_MODERN.md) and
[HTTP setup](MCP_STREAMABLE_HTTP.md).

The modern HTTP implementation uses the JSON response option. SSE, HTTP
disconnect cancellation, progress streams, subscriptions, MRTR, and OAuth
discovery are outside this implementation. Synchronous workers need cooperative
cancellation; cancelling their awaiting task cannot kill host Python code.

## Execution isolation

`DockerPythonToolkit` adds full Python execution in a trusted, already installed
Linux image. Each invocation has a read-only root, restricted writable tmpfs,
no host mounts, no network, non-root execution, dropped capabilities, no new
privileges, and CPU/memory/PID limits. Source/output budgets and timeout cleanup
bound the CLI process. It never pulls an image or falls back to host execution.
It validates the Docker context's local socket/pipe before execution and pins
that endpoint for engine inspection, execution, and cleanup. Remote contexts
are rejected before source code is supplied to the daemon.

The existing `PythonToolkit`, shell toolkit, CLI permissions, and TUI defaults
are unchanged. Container restrictions are an opt-in boundary, not a guarantee
against kernel or container-runtime vulnerabilities. See
[execution isolation](EXECUTION_ISOLATION.md).

## Provider hardening

The compatible, Hugging Face, DeepInfra, DashScope, and Responses adapters now
reject malformed endpoints, invalid option types, unsupported media, protected
request overrides, and malformed native streams/calls before inference or tool
execution. Native reasoning and call-ID replay remain intact. See
[provider hardening](PROVIDER_HARDENING.md).

## Evidence and limits

The combined suite covers the preceding implementation phases and the new
runtime, protocol, provider, and isolation paths. Installed-wheel tests run
copied tests in isolated Python mode without the repository's source fixture.
The wheel and source distribution include all four modern scaffold files,
including `.env.example`.

Local Docker execution was skipped because the daemon is stopped. The dedicated
Linux CI job prepares an official Python image and runs the real container test.
That test verifies actual identity, missing synthetic credentials, loopback-only
interfaces, filesystem/mount restrictions, cgroup limits, and timeout removal.
The runtime itself never prepares or downloads images.

Authorized live Groq tests used synthetic calculator data and a 512-token output
budget. Parallel and mixed batches passed. The first sequential attempt returned
a plan-validation failure; an unchanged-prompt retry passed all four dependent
outputs. Both the failed attempt and retry are retained in the evidence rather
than treating the model as deterministic. No other live provider inference was
performed.

The first combined CI run exposed reverse-DNS delays on macOS and a test reading
a newly selected TUI conversation before mounting completed. HTTP startup now
avoids the reverse lookup, and the TUI check waits for observable readiness.
These fixes have focused regressions. Final counts and CI links are recorded
in the completion evidence below.

| Verification | Result |
|---|---|
| Complete local non-live suite | 1,029 passed, 2 skipped, 11 live tests deselected |
| Isolated installed-wheel tests | 241 passed, 1 Docker test skipped locally |
| Provider boundary/TUI/native SDK contracts | 160 passed |
| Modern core, stdio, and HTTP contracts | 89 passed |
| Isolation plus existing toolkit regressions | 80 passed, 1 local Docker skip |
| Real Docker CI test file | 48 passed, including kernel-enforced restriction checks |
| TUI readiness follow-up | 84 passed |
| Focused Ruff, byte compilation, dependency and docs checks | Passed |
| Wheel and source archive, including environment scaffold | Built and checked |

The local non-live skips are the optional Streamlit module and opt-in Docker
execution. Cohere emitted one upstream Pydantic deprecation warning.

The [final CI matrix](https://github.com/GodBoii/Model-Tool-protocol-/actions/runs/37307415317)
passed all six jobs for implementation/test commit `982eb99`: fast tests on
Windows, Linux, and macOS, provider integration tests on Windows and Linux, and
real Docker restriction checks on Linux. That commit also passed
[docs consistency](https://github.com/GodBoii/Model-Tool-protocol-/actions/runs/37307415294)
and [MCP smoke](https://github.com/GodBoii/Model-Tool-protocol-/actions/runs/37307415316).

[Structured verification evidence](audits/implementation-phases-3-5-2026-10-05/verification.json)
records the counts, limits, and CI run. Live Groq evidence retains the
[first attempt](audits/implementation-phases-3-5-2026-10-05/groq-first-attempt.json)
and [sequential retry](audits/implementation-phases-3-5-2026-10-05/groq-sequential-retry.json).
No keys, headers, or workspace content are included.

Publishing to PyPI remains pending. The report/evidence commit following the
verified implementation changes documentation only.
