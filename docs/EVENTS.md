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

## Base fields

Every event includes:
- `type`: event kind
- `timestamp`: ISO 8601 UTC timestamp
- `run_id`: stable id for the run
- `sequence`: monotonic event index within the run
- Optional run context fields on `run_started`: `user_id`, `session_id`, `metadata`

## Event types

- `run_started`
  - `user_message`
  - `max_rounds`
  - `tools_available`
  - `tool_names`

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

## Why this is provider-agnostic

Providers only produce actions/plans and optional token streams.  
The Agent owns event emission, so frontend code can consume one stable event model regardless of provider.
