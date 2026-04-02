# Groq Integration Guide

## Overview

MTP includes `GroqToolCallingProvider` to run model-native tool calls with the Groq Chat Completions API.

It supports:
- mapping `ToolSpec` into Groq function-tool schema
- converting Groq `tool_calls` into MTP `ExecutionPlan`
- sending tool results back as `role="tool"` messages with `tool_call_id`
- multi-round execution support via `Agent.run_loop(max_rounds=N)`
- optional strict dependency guidance (`strict_dependency_mode=True`)
- optional reasoning controls (`include_reasoning`, `reasoning_format`, `reasoning_effort`)
- streaming usage capture (`stream_options.include_usage`) for richer debug metrics

## Install

```bash
pip install groq
pip install python-dotenv
```

## Environment

Create `.env` from `.env.example`:

```env
GROQ_API_KEY=your_groq_api_key_here
```

Use provider-agnostic config loading once at app startup:

```python
from mtp import load_dotenv_if_available

load_dotenv_if_available()  # checks .env first, then .env.example
```

## Minimal usage

```python
from mtp import Agent, ToolRegistry, ToolSpec
from mtp import load_dotenv_if_available
from mtp.providers import Groq

load_dotenv_if_available()

registry = ToolRegistry()
registry.register_tool(
    ToolSpec(name="github.list_repos", description="List repos", input_schema={"type": "object"}),
    lambda username: {"repos": ["mtp-core"]},
)

provider = Groq(strict_dependency_mode=True)
agent = Agent(provider=provider, tools=registry)
print(agent.run("List repos for username demo-user"))
```

Reasoning + streaming usage example:

```python
provider = Groq(
    model="moonshotai/kimi-k2-instruct",
    include_reasoning=True,
    reasoning_format="parsed",
    reasoning_effort="medium",
    stream_include_usage=True,
)
```

## Current behavior

1. Agent sends messages + tool schemas to Groq.
2. If Groq emits `tool_calls`, MTP builds one parallel batch.
3. Runtime executes tools.
4. Agent sends tool results back to Groq for final text.

Current scope is one model-driven tool round plus final response. Multi-round chaining is on roadmap.

## Troubleshooting

- `ImportError: groq is not installed`
  - install with `pip install groq`
- `Environment variable GROQ_API_KEY is required`
  - set `GROQ_API_KEY` in shell or `.env`
- model returns no tool call
  - improve tool description/schema and system prompt specificity
