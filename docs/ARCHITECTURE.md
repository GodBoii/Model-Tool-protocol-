# MTP Python Architecture (v0.1)

## Layered design

1. `mtp.protocol`
- Defines tool metadata, call objects, results, and execution plans.
- Adds fields needed for practical orchestration: risk hints, cache TTL, dependencies.

2. `mtp.schema`
- Provides the protocol envelope (`MessageEnvelope`) with `mtp_version`.
- Validates plans before runtime execution:
  - duplicate call IDs
  - missing dependencies
  - cyclic dependency graphs

3. `mtp.policy`
- Provides risk-aware policy decisions (`allow`, `ask`, `deny`).
- Enables explicit behavior for destructive tools and human approval workflows.

4. `mtp.runtime`
- Maintains tool registry and toolkit loaders.
- Supports lazy toolkit loading by tool prefix (example: `github.*`).
- Supports tool spec preview from loaders so providers can discover tools before handlers load.
- Supports dynamic tool mutation via registry-level `add_tool` / `set_tools`.
- Executes `ExecutionPlan` batches in sequential or parallel mode.
- Resolves inter-call references via `{ "$ref": "<call_id>" }`.
- Applies result caching with TTL.
- Enforces policy decisions before tool invocation.
- Supports `ASK` approval flow via optional `approval_handler`.
- Propagates tool control-flow exceptions (`RetryAgentRun`, `StopAgentRun`) to the agent loop.

5. `mtp.agent`
- Generic agent loop:
  - gather tools
  - request plan from provider adapter
  - execute plan
  - return tool results to provider for final response
- Includes `run_loop(max_rounds=N)` for multi-round tool chaining.
- Includes async APIs: `arun()` / `arun_loop()` / `arun_loop_events(...)`.
- Supports optional async provider hooks (`anext_action`, `afinalize`) with sync fallback.
- Includes `run_loop_stream(...)` for text streaming.
- Includes `run_loop_events(...)` for structured runtime event streaming.
- Supports `continue_run(...)` / `acontinue_run(...)` for paused-run continuation.
- Supports structured input validation (`input_schema`) and output refinement pipeline (`output_model`, `parser_model`).
- Optional strict dependency enforcement (`strict_dependency_mode=True`) to reject guessed intermediate values in same-toolkit multi-call batches.
- Injects internal MTP system instructions automatically; user instructions are layered on top.

6. `mtp.providers`
- Provider adapter interface for OpenAI/Anthropic/Gemini/Groq/etc.
- Current repo includes:
  - deterministic local planner (`SimplePlannerProvider`) for demo/testing
  - `GroqToolCallingProvider` for real model-driven tool calls
- Providers are instantiated explicitly by users and passed into `Agent`/`MTPAgent`.

Cross-provider configuration note:
- API key loading is intentionally provider-agnostic in `mtp.config` (`load_dotenv_if_available`, `require_env`).
- Providers read environment variables but do not own dotenv behavior.

7. `mtp.toolkits`
- Local toolkits included:
  - `calculator`: basic arithmetic operations
  - `file`: list/read/write/search
  - `python`: run code and files in constrained context
  - `shell`: run local shell commands
- Local toolkit parameter schemas accept `{"$ref":"<tool_call_id>"}` values to enable dependency wiring in model-generated arguments.
- Custom toolkit APIs:
  - `@mtp_tool` decorator for Python functions
  - `toolkit_from_functions(...)`
  - `FunctionToolkit` for reusable toolkit loaders

8. `mtp.transport`
- `stdio` transport for line-delimited JSON envelopes.
- HTTP transport server for envelope POST roundtrips.

9. `mtp.simple_agent`
- `MTPAgent` provides high-level ergonomic construction:
  - provider-agnostic wrapper around `Agent`
  - explicit provider + registry injection
  - short `run()` / `print_response()` interface

10. `mtp.session_store`
- Optional session persistence abstraction (`SessionStore` protocol).
- Built-in stores:
  - `JsonSessionStore`
  - `PostgresSessionStore`
  - `MySQLSessionStore`
- Persists message history and run summaries keyed by `session_id`.

## Module boundaries

- `mtp.providers`:
  - Converts model-native responses into `AgentAction`
  - Does not execute tools
  - Does not own file system or toolkit logic

- `mtp.toolkits`:
  - Owns tool specs + handlers for domain capabilities
  - Can expose spec previews via `list_tool_specs()` for lazy discovery
  - Should avoid provider-specific assumptions

- `mtp.runtime`:
  - Single source of truth for plan execution
  - Handles lazy loading, caching, refs, and policy checks

- `mtp.agent`:
  - Orchestration loop only
  - No provider-specific tool parsing logic
  - Delegates persistence to `session_store` when configured

- `mtp.transport`:
  - Message ingress/egress only
  - Uses `MessageEnvelope` serialization
  - No business logic execution

## Protocol direction

Implemented:
- Standardized plan format for dependency-aware batches.
- Cache TTL semantics in tool specs.
- Lazy toolkit loading and execution in one runtime.
- Plan validation (including cycle checks).
- Risk policy hooks.
- Groq provider + dotenv loading support.
- Local no-key toolkits for calculator/file/python/shell.
- Multi-round execution loop in agent.
- Continue/pause run primitives with tool-driven control flow.
- Structured input schema validation.
- Output model + parser model refinement pipeline.
- Envelope transport primitives (stdio + HTTP).
- Session persistence via JSON/PostgreSQL/MySQL stores.

Next steps:
- JSON schema + versioned wire format for MTP messages.
- Transport abstraction (stdio/http/ws).
- Streaming result chunks for long-running tools.
- Provider capability matrix and richer per-provider options.

See:
- [Storage and Sessions](C:\Users\prajw\Downloads\MTP\docs\STORAGE.md)
