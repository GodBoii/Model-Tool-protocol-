# Event Stream Contract

MTP exposes a provider-agnostic event stream through:

```python
agent.run_events(prompt, max_rounds=5, stream_final=True)
```

or:

```python
agent.print_response(prompt, stream=True, stream_events=True)
```

All providers map into the same event shape.

`print_response(..., stream_events=True)` now defaults to human-readable terminal formatting.
Use `event_format="json"` to print raw JSON lines.

## Base fields

Every event includes:
- `type`: event kind
- `timestamp`: ISO 8601 UTC timestamp
- `run_id`: stable id for the run
- `sequence`: monotonic event index within the run
- Optional run context fields on `run_started`: `user_id`, `session_id`, `metadata`
- Optional validation field on `run_started`: `input_validation_error`

## Event types

- `run_started`
  - `user_message`
  - `max_rounds`
  - `tools_available`
  - `tool_names`
  - `system_instructions`
  - `member_agents`: list of `{id, mode, delegation_tool, instructions, system_instructions, tools}`

- `round_started`
  - `round`

- `plan_received`
  - `round`
  - `batches`: list of `{mode, calls, call_ids}`

- `strict_violations`
  - `round`
  - `violations`: list of `{call_id, tool_name, message}`

- `assistant_tool_message`
  - `round`
  - `message`: raw assistant tool call message

- `batch_started`
  - `round`
  - `batch_index`
  - `mode`
  - `call_ids`

- `tool_started`
  - `round`
  - `batch_index`
  - `call_id`
  - `tool_name`
  - `arguments`
  - `depends_on`

- `tool_finished`
  - `round`
  - `call_id`
  - `tool_name`
  - `success`
  - `cached`
  - `approval`
  - `output`
  - `error`

- `text_chunk`
  - `chunk`
  - `source`: `direct | finalize_stream | finalize_fallback`

- `run_completed`
  - `final_text`
  - `rounds`
  - `total_tool_calls`

- `run_cancelled`
  - `round`

- `tool_retry_requested`
  - `round`
  - `tool_name`
  - `feedback`

- `run_paused`
  - `round`
  - `reason`
  - `tool_name`

## Why this is provider-agnostic

Providers only produce actions/plans and optional token streams.  
The Agent owns event emission, so frontend code can consume one stable event model regardless of provider.
