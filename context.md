# MTP Project Context

## Project Intent
- Build an open, protocol-first Python agent SDK around MTP (Model Tool Protocol), not just an MCP adapter.
- Support provider-agnostic tool orchestration with:
  - lazy toolkit loading
  - dependency-aware batch execution (parallel + sequential)
  - strict dependency mode (`$ref` / `depends_on`)
  - streaming (text + structured events)
  - pluggable providers and custom tools/toolkits

## Current Local Repository
- Root: `C:\Users\prajw\Downloads\MTP`
- Key folders:
  - `src/mtp` (core SDK code)
  - `docs` (architecture and usage docs)
  - `examples` (working examples)
  - `tests` (existing validation suite)
  - `agno_library` (local Agno reference install/venv)
- Key files:
  - `README.md`
  - `pyproject.toml`
  - `.env`, `.env.example`
  - `my_thoughts.txt`

## Local Agno Reference (for implementation inspiration)
- Folder: `C:\Users\prajw\Downloads\MTP\agno_library`
- Notable paths used for reference:
  - `agno_library/agno/Lib/site-packages/agno/models/groq/groq.py`
  - `agno_library/agno/Lib/site-packages/agno/agent/_tools.py`
- This folder is a local reference environment, not MTP production code.

## Core MTP Modules (Current)
- `src/mtp/protocol.py`:
  - `ToolSpec`, `ToolCall`, `ToolBatch`, `ExecutionPlan`, `ToolResult`
- `src/mtp/runtime.py`:
  - registry, lazy toolkit loading, cache, batch execution, ref resolution
- `src/mtp/agent.py`:
  - run loop, strict dependency enforcement, streaming, event streaming
- `src/mtp/events.py`:
  - common provider-agnostic event envelope context (`run_id`, `sequence`)
- `src/mtp/strict.py`:
  - strict dependency validation
- `src/mtp/schema.py`:
  - `MessageEnvelope`, serialization helpers, plan validation
- `src/mtp/providers/`:
  - `groq_provider.py`
  - `mock.py`
  - `simple_planner.py`
- `src/mtp/toolkits/`:
  - `calculator.py`, `file_toolkit.py`, `python_toolkit.py`, `shell_toolkit.py`
  - `local.py` (aggregator helper)
  - `common.py` (schema helpers)
- `src/mtp/tools.py`:
  - custom tool creation (`@mtp_tool`, `FunctionToolkit`, `toolkit_from_functions`)
- `src/mtp/simple_agent.py`:
  - provider-agnostic convenience wrapper requiring explicit provider + registry

## Current Public API Direction
- Explicit provider initialization (no default provider coupling).
- Explicit toolkit/registry initialization.

## Example Scripts
- `examples/groq_agent.py`:
  - explicit Groq provider + explicit toolkit registration + streaming
- `examples/groq_agent_events.py`:
  - structured event stream JSON lines
- `examples/custom_toolkit_agent.py`:
  - custom Python functions as tools/toolkit
- `examples/quickstart.py`:
  - local mock planner flow

## Documentation Map
- `docs/QUICKSTART.md`
- `docs/ARCHITECTURE.md`
- `docs/PROTOCOL_SPEC.md`
- `docs/LOCAL_TOOLKITS.md`
- `docs/CREATING_TOOLS.md`
- `docs/PROVIDERS.md`
- `docs/GROQ_INTEGRATION.md`
- `docs/EVENTS.md`
- `docs/TRANSPORT.md`
- `docs/ROADMAP.md`
- `docs/PUBLISHING.md`
- `docs/IMPLEMENTATION_NOTES.md`

## External References Used

### Groq
- Quickstart: https://console.groq.com/docs/quickstart
- Libraries: https://console.groq.com/docs/libraries
- Tool use: https://console.groq.com/docs/tool-use
- Local tool calling: https://console.groq.com/docs/tool-use/local-tool-calling
- PyPI package: https://pypi.org/project/groq/
- SDK repo: https://github.com/groq/groq-python

### Agno (tool creation + toolkit patterns)
- Overview: https://docs.agno.com/tools/creating-tools/overview
- Python functions: https://docs.agno.com/tools/creating-tools/python-functions
- Toolkits: https://docs.agno.com/tools/creating-tools/toolkits
- Local calculator toolkit: https://docs.agno.com/tools/toolkits/local/calculator
- Local file toolkit: https://docs.agno.com/tools/toolkits/local/file
- Local python toolkit: https://docs.agno.com/tools/toolkits/local/python
- Local shell toolkit: https://docs.agno.com/tools/toolkits/local/shell

### MCP / broader protocol prior art
- MCP spec: https://modelcontextprotocol.io/specification/2025-06-18
- MCP schema: https://modelcontextprotocol.io/specification/2025-06-18/schema

## Environment/Execution Notes
- Global Python on this machine may not have `groq` installed.
- Local Agno venv is usable for live Groq runs:
  - `agno_library\agno\Scripts\python ...`
- `.env` / `.env.example` used for API key loading via `load_dotenv_if_available()`.

## Current Design Principles
- Provider-agnostic core
- Explicit dependency wiring for correctness
- Clear separation:
  - providers
  - runtime/agent orchestration
  - toolkits/tools
  - transport
  - events
- Keep examples practical but non-coupling for future providers.


read all the docs, codes and analyze the entire codebase
<context>
[agno_library\agno\Lib\site-packages\agno\models\anthropic\claude.py
agno_library\agno\Lib\site-packages\agno\models\google\gemini.py
agno_library\agno\Lib\site-packages\agno\models\openai\responses.py
agno_library\agno\Lib\site-packages\agno\models\openrouter\openrouter.py
agno_library\agno\Lib\site-packages\agno\models\groq\groq.py
agno_library\agno\Lib\site-packages\agno\agent\agent.py]
</context>
"https://docs.agno.com/reference/agents/agent" , "https://docs.agno.com/agents/running-agents", "https://docs.agno.com/run-cancellation/agent-cancel-run" , "https://docs.agno.com/agents/usage/agent-with-tools" , "https://docs.agno.com/input-output/structured-input/agent", "https://docs.agno.com/input-output/output-model" , "https://docs.agno.com/tools/agent" , "https://github.com/agno-agi/agno/tree/main/cookbook" , "https://docs.agno.com/tools/exceptions" , "https://docs.agno.com/tools/toolkits/overview" , "https://docs.agno.com/tools/attaching-tools" , "https://docs.agno.com/teams/overview" , "https://docs.agno.com/teams/building-teams" , "https://docs.agno.com/teams/running-teams" , "https://docs.agno.com/teams/delegation" , "https://docs.agno.com/teams/debugging-teams" , "https://docs.agno.com/teams/usage/streaming" , "https://docs.agno.com/teams/usage/respond-directly" , "https://docs.agno.com/examples/basics/multi-agent-team"