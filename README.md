# MTPX

[![PyPI version](https://badge.fury.io/py/mtpx.svg)](https://pypi.org/project/mtpx/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

MTPX is a Python SDK and CLI for building agents that can use tools without turning your application into a pile of prompt glue.

It gives you a small runtime for model-tool-model loops, provider adapters for popular LLM APIs, local toolkits for files and code work, session storage, streaming events, and MCP-compatible transports when you need interoperability.

## Why MTPX

- Build agents with explicit tool contracts instead of hidden ad hoc callbacks.
- Run dependency-aware batches of tool calls, including references between calls.
- Swap providers without rewriting your agent loop.
- Stream text, reasoning, tool events, and usage metadata from one runtime.
- Persist sessions in JSON, Postgres, or MySQL.
- Expose the same tools through SDK code, a CLI/TUI, Streamlit Agent OS, or MCP JSON-RPC.

MTPX is still alpha, but the shape is practical: install it, register tools, choose a provider, and start building.

## Install

```bash
pip install mtpx
```

Common extras:

```bash
pip install "mtpx[groq,dotenv]"
pip install "mtpx[openai,anthropic,dotenv]"
pip install "mtpx[ollama,lmstudio]"
pip install "mtpx[toolkits-web]"
pip install "mtpx[stores-db]"
pip install "mtpx[all]"
```

From source:

```bash
git clone https://github.com/GodBoii/Model-Tool-protocol-.git
cd Model-Tool-protocol-
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dotenv,groq]"
```

MTPX does not load `.env` automatically. Install `python-dotenv` and call `Agent.load_dotenv_if_available()`, or export provider keys in your shell.

## SDK Quickstart

```python
from mtp import Agent
from mtp.providers import Groq
from mtp.toolkits import CalculatorToolkit, FileToolkit

Agent.load_dotenv_if_available()

tools = Agent.ToolRegistry()
tools.register_toolkit_loader("calculator", CalculatorToolkit())
tools.register_toolkit_loader("file", FileToolkit(base_dir="."))

agent = Agent.MTPAgent(
    provider=Groq(model="llama-3.3-70b-versatile"),
    tools=tools,
    instructions="Use tools when they improve correctness. Keep answers concise.",
    strict_dependency_mode=True,
)

print(agent.run("What is 25 * 4 + 10? Then list the project files.", max_rounds=4))
```

Streaming:

```python
agent.print_response(
    "Inspect this project and summarize the CLI entry points.",
    max_rounds=6,
    stream=True,
    stream_events=True,
)
```

Persistent sessions:

```python
from mtp import Agent, JsonSessionStore
from mtp.providers import OpenAI

store = JsonSessionStore(db_path="tmp/mtp_json_db")
agent = Agent.MTPAgent(provider=OpenAI(model="gpt-4o"), tools=tools, session_store=store)

agent.run("Remember: project codename is Atlas.", session_id="chat-1", user_id="u1")
agent.run("What is the project codename?", session_id="chat-1", user_id="u1")
```

User-owned sessions must be read with the same `user_id`; this prevents accidental cross-user session reads.

## CLI Quickstart

```bash
mtp --help
mtp providers list
mtp doctor
mtp new my-agent --template minimal
mtp run my-agent
```

Interactive terminal agent:

```bash
pip install "mtpx[groq,dotenv]"
mtp tui
```

Streamlit Agent OS:

```bash
pip install "mtpx[dotenv,ui-streamlit,groq,openai,openrouter]"
mtp agent-os
```

Agent OS starts with safer defaults: calculator and file tools are enabled first, while shell/Python/web tools are opt-in from the sidebar.

## Providers

Supported adapters include:

- OpenAI
- Groq
- Anthropic
- Gemini
- OpenRouter
- Mistral
- Cohere
- Cerebras
- DeepSeek
- SambaNova
- Together AI
- Fireworks AI
- Xiaomi MiMo
- Ollama
- LM Studio
- Mock planner for deterministic tests

Xiaomi MiMo uses `MIMO_API_KEY`. You can override its endpoint with `MIMO_BASE_URL`, and live tests require both `MIMO_API_KEY` and `RUN_LIVE_XIAOMI=1`.

## Toolkits

Built-in toolkit loaders:

- `CalculatorToolkit`: arithmetic helpers.
- `FileToolkit`: scoped file listing, reading, writing, and search.
- `PythonToolkit`: isolated subprocess execution for code snippets/files.
- `ShellToolkit`: bare-command allowlist execution in a scoped directory.
- `WebsiteToolkit`: public HTTP/HTTPS page text extraction with private-network blocking by default.
- `WikipediaToolkit`, `NewspaperToolkit`, `Newspaper4kToolkit`, `Crawl4aiToolkit`: optional web research helpers.

Local code execution tools are powerful. Keep their base directory narrow, use policy controls, and do not expose them to untrusted users without an approval layer.

## MCP And Transports

MTPX includes:

- line-delimited stdio envelopes
- HTTP envelope transport
- optional WebSocket transport
- MCP-compatible JSON-RPC server
- MCP HTTP and WebSocket transports with progress replay

MCP auth is explicit: `require_auth=True` must be paired with an `auth_token`, `auth_validator`, or `auth_provider`.

## Testing

Fast local checks:

```bash
python -m pytest -q -m "not integration and not live"
python -m compileall -q src tests
```

Integration checks:

```bash
python -m pytest -q -m "integration and not live"
```

Live provider tests are opt-in. For Xiaomi:

```bash
set RUN_LIVE_XIAOMI=1
set MIMO_API_KEY=...
python -m pytest -q tests/test_e2e_xiaomi.py
```

## Docs

- [Quickstart](docs/QUICKSTART.md)
- [Agent API Reference](docs/AGENT_API.md)
- [Storage and Sessions](docs/STORAGE.md)
- [Providers](docs/PROVIDERS.md)
- [Provider Guides](docs/PROVIDER_GUIDES.md)
- [Local Inference](docs/LOCAL_INFERENCE.md)
- [Local Toolkits](docs/LOCAL_TOOLKITS.md)
- [Creating Tools](docs/CREATING_TOOLS.md)
- [Events Contract](docs/EVENTS.md)
- [Transport](docs/TRANSPORT.md)
- [MCP Interop Adapter](docs/MCP_INTEROP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Testing](docs/TESTING.md)
- [Publishing](docs/PUBLISHING.md)

## Repository Map

- `src/mtp/agent.py`: agent loop, streaming, sessions, orchestration modes.
- `src/mtp/runtime.py`: tool registry, lazy toolkit loading, policy checks, execution plans.
- `src/mtp/protocol.py`: protocol dataclasses.
- `src/mtp/schema.py`: envelope and argument validation.
- `src/mtp/providers/`: LLM provider adapters.
- `src/mtp/toolkits/`: local and web toolkit loaders.
- `src/mtp/cli/`: CLI, TUI, scaffolding, doctor, Agent OS launcher.
- `src/mtp/mcp.py`: MCP-compatible JSON-RPC adapter.
- `src/mtp/mcp_transport.py`: MCP HTTP/WebSocket transports.
- `docs/`: guides and reference material.
- `examples/`: runnable provider and toolkit examples.

## License

MIT License. See [LICENSE](LICENSE).
