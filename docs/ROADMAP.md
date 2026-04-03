# MTP Roadmap (Python)

## Phase 0 (current)
- Protocol objects for tools, calls, results, and plans.
- Runtime for lazy loading, parallel/sequential batches, call dependencies.
- TTL caching for repeat calls.
- Provider adapter interface and local planner.
- Basic tests and quickstart.

## Phase 1 (implemented)
- Versioned wire schema baseline:
  - `mtp_version` envelope (`MessageEnvelope`)
- Deterministic plan validator:
  - no duplicate call IDs
  - cycle detection
  - dependency edge validation
- Approval policy hooks:
  - allow / ask / deny by risk level
  - per-tool override via `by_tool_name`
- Provider adapter baseline:
  - model-native tool calls
  - dotenv loading support
  - adapters for Groq/OpenAI/OpenRouter/Gemini/Anthropic/SambaNova and additional optional providers
- Local toolkit package:
  - calculator
  - file
  - python
  - shell
- Agent multi-round execution:
  - `run_loop(max_rounds=N)`
- Run continuation:
  - `continue_run(...)` / `acontinue_run(...)`
- Structured input/output:
  - `input_schema` validation
  - `output_schema` parsing/validation
- Output refinement pipeline:
  - `output_model` + `parser_model`
- Dynamic tool mutation:
  - `add_tool(...)` / `set_tools(...)`
- Tool control-flow exceptions:
  - `RetryAgentRun`
  - `StopAgentRun`
- Async provider hooks:
  - `anext_action(...)`
  - `afinalize(...)`
- Transport scaffolding:
  - stdio envelope transport
  - HTTP envelope transport
- Persistent session store:
  - `JsonSessionStore`
  - `PostgresSessionStore`
  - `MySQLSessionStore`

## Phase 2
- Provider depth:
  - richer per-provider feature flags
  - native structured output modes where available
  - advanced token/usage/trace metadata
- Planner modes:
  - direct model-native tool calls
  - model-generated MTP plan mode
- advanced multi-round policies:
  - adaptive stop conditions
  - budget-aware continuation

## Phase 3
- Advanced transport and remote execution:
  - remote tool servers and cross-process execution patterns
  - streamable transport upgrades (for long-running workflows and resumability)
  - pluggable transport expansion beyond current stdio/HTTP envelope primitives
- Unified tracing events for all tool calls.
- Rich analytics/query APIs on top of persisted session data.

## Phase 4
- Developer experience:
  - `mtp new` project template
  - tool decorator package (`@mtp_tool`)
  - docs site with runnable examples and cookbook
  - integration test matrix across providers
