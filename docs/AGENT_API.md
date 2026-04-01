# Agent API Reference

This document is the canonical reference for `Agent` and `MTPAgent`.

## Constructor

Source: [agent.py](/c:/Users/prajw/Downloads/MTP/src/mtp/agent.py)

```python
Agent(
    provider: ProviderAdapter,
    registry: ToolRegistry | None = None,
    *,
    tools: ToolRegistry | None = None,
    debug_mode: bool = False,
    debug_logger: Callable[[str], None] | None = None,
    debug_max_chars: int = 600,
    strict_dependency_mode: bool = False,
    instructions: str | None = None,
    system_instructions: str | None = None,
    stream_chunk_size: int = 40,
    max_history_messages: int = 200,
    mode: str = "standalone",
    members: dict[str, Agent] | None = None,
)
```

`mode` options:
- `standalone` (default): regular single-agent behavior.
- `member`: marks this instance as a delegated sub-agent.
- `delegator` / `orchestration`: enables member delegation tools.

When `mode` is `delegator` or `orchestration`, each member is exposed as a tool:
- `agent.member.<member_name>`
- input: `{"task": ..., "max_rounds": 5, "tool_call_limit": ...}`
- output: member agent final text response.

## Runtime methods

### Basic execution

- `run(user_input: Any) -> str`
- `arun(user_input: Any) -> str`
- `run_loop(user_input: Any, max_rounds: int = 5, *, tool_call_limit: int | None = None, input_schema: dict | None = None) -> str`
- `arun_loop(...) -> str`

`user_input` can be a string, dict, list, or model-like object (`model_dump`/`dict` supported).

### Structured input

- `input_schema` is supported on:
  - `run_loop`
  - `arun_loop`
  - `run_output`
  - `arun_output`
  - `run_stream`/events wrappers via `MTPAgent`

If validation fails, run exits early with `RunOutput.output_validation_error`.

### Structured output

- `output_schema` is supported on:
  - `run_output`
  - `arun_output`

When provided, MTP parses final text as JSON and validates against the schema.

### Output model pipeline

`run_output` and `arun_output` support:

- `output_model`
- `output_model_prompt`
- `parser_model`
- `parser_model_prompt`

Pipeline order:
1. primary run generates final text
2. optional `output_model` refines it
3. optional `parser_model` post-processes it
4. optional `output_schema` validation runs

### RunOutput

Source: [agent.py](/c:/Users/prajw/Downloads/MTP/src/mtp/agent.py)

Fields:

- `run_id`
- `input`
- `final_text`
- `messages`
- `tool_results`
- `user_id`
- `session_id`
- `metadata`
- `cancelled`
- `total_tool_calls`
- `output`
- `output_validation_error`
- `paused`
- `pause_reason`

### Cancellation and continuation

- `cancel_run(run_id: str) -> bool`
- `continue_run(run_output: RunOutput | None = None, run_id: str | None = None, ...) -> RunOutput`
- `acontinue_run(...) -> RunOutput`

Paused runs (for example from `StopAgentRun`) can be resumed by `run_id` or prior `RunOutput`.

### Streaming

- `run_loop_stream(...) -> Iterator[str]`
- `run_loop_events(...) -> Iterator[dict]`
- `arun_loop_events(...) -> AsyncIterator[dict]`

`MTPAgent.print_response(..., stream_events=True)` prints event JSON lines.

### Dynamic tool management

- `add_tool(tool: RegisteredTool | Callable) -> None`
- `set_tools(tools: list[RegisteredTool | Callable]) -> None`

This enables post-initialization tool updates without rebuilding the agent.

## Tool control-flow exceptions

Source: [exceptions.py](/c:/Users/prajw/Downloads/MTP/src/mtp/exceptions.py)

- `RetryAgentRun("feedback")`: injects feedback and asks the model to replan.
- `StopAgentRun("reason")`: pauses/stops the current run and returns with `paused=True`.

## Async provider contract

Providers may now implement optional async hooks:

- `anext_action(...)`
- `afinalize(...)`

If not implemented, agent async APIs use thread fallback for sync provider methods.

## MTPAgent wrapper

Source: [simple_agent.py](/c:/Users/prajw/Downloads/MTP/src/mtp/simple_agent.py)

`MTPAgent` mirrors the same features:

- `run`, `arun`
- `run_output`, `arun_output`
- `run_stream`
- `run_events`, `arun_events`
- `continue_run`, `acontinue_run`
- `cancel_run`
- `print_response`
