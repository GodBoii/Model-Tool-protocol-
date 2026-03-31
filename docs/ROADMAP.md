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
- Groq provider adapter:
  - model-native tool calls
  - dotenv loading support
  - one-round tool execution + final response

## Phase 2
- Additional provider adapters:
  - OpenAI Responses API
  - Anthropic Messages API
  - Gemini function calling
  - Groq-compatible OpenAI schema mode
- Planner modes:
  - direct model-native tool calls
  - model-generated MTP plan mode
- Multi-round tool-call loop (`max_rounds`) with stop conditions

## Phase 3
- Transport and remote execution:
  - stdio and HTTP transports
  - remote tool servers
  - streamable partial results for long-running tools
- Unified tracing events for all tool calls.

## Phase 4
- Developer experience:
  - `mtp new` project template
  - tool decorator package (`@mtp_tool`)
  - docs site with runnable examples and cookbook
  - integration test matrix across providers
