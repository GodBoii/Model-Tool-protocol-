# MTP Protocol Spec (Draft v0.1.0)

## Scope

This draft defines the in-process protocol model used by `mtp-python`.

## Core entities

## `ToolSpec`
- `name`: stable tool identifier (`toolkit.action` recommended)
- `description`: model-facing description
- `input_schema`: JSON schema-like object for tool args
- `risk_level`: `read_only` | `write` | `destructive`
- `cache_ttl_seconds`: optional cache hint for runtime reuse

## `ToolCall`
- `id`: unique call identifier inside a plan
- `name`: selected tool name
- `arguments`: dict payload
- `depends_on`: list of call IDs required before this call

## `ToolBatch`
- `mode`: `parallel` or `sequential`
- `calls`: list of `ToolCall`

## `ExecutionPlan`
- `batches`: ordered list of batches
- `metadata`: provider/planner metadata

## `ToolResult`
- `call_id`, `tool_name`, `output`
- `success`, `error`
- `cached`
- `approval`: policy decision used (`allow`/`ask`/`deny`)
- `skipped`: true when blocked by policy

## Envelope

`MessageEnvelope` provides a lightweight versioned wrapper:
- `mtp_version`
- `kind`
- `payload`
- `metadata`

## Validation rules

`validate_execution_plan(plan)` enforces:
1. no duplicate `ToolCall.id`
2. every dependency references an existing call ID
3. dependency graph is acyclic

## Execution semantics

1. Validate plan.
2. Execute batches in order.
3. For sequential batches, execute calls in listed order.
4. For parallel batches, execute all listed calls concurrently.
5. Before execution, resolve argument references:
   - `{ "$ref": "<call_id>" }` replaces value with prior result output.
6. Enforce risk policy.
7. Apply cache lookup/store if TTL configured.

## Non-goals in v0.1.0

- transport protocol definition (HTTP/stdio/ws)
- cryptographic signing/auth
- streaming partial tool result chunks
- formal RFC process
