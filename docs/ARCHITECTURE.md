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
- Executes `ExecutionPlan` batches in sequential or parallel mode.
- Resolves inter-call references via `{ "$ref": "<call_id>" }`.
- Applies result caching with TTL.
- Enforces policy decisions before tool invocation.

5. `mtp.agent`
- Generic agent loop:
  - gather tools
  - request plan from provider adapter
  - execute plan
  - return tool results to provider for final response

6. `mtp.providers`
- Provider adapter interface for OpenAI/Anthropic/Gemini/Groq/etc.
- Current repo includes:
  - deterministic local planner (`SimplePlannerProvider`) for demo/testing
  - `GroqToolCallingProvider` for real model-driven tool calls

Cross-provider configuration note:
- API key loading is intentionally provider-agnostic in `mtp.config` (`load_dotenv_if_available`, `require_env`).
- Providers read environment variables but do not own dotenv behavior.

## Protocol direction

Implemented:
- Standardized plan format for dependency-aware batches.
- Cache TTL semantics in tool specs.
- Lazy toolkit loading and execution in one runtime.
- Plan validation (including cycle checks).
- Risk policy hooks.
- Groq provider + dotenv loading support.

Next steps:
- JSON schema + versioned wire format for MTP messages.
- Transport abstraction (stdio/http/ws).
- Approval policy hooks based on risk level.
- Streaming result chunks for long-running tools.
- More provider adapters and multi-round tool-call loops.
