 MTPX (Model Tool Protocol Extended)

[![PyPI version](https://badge.fury.io/py/mtpx.svg)](https://pypi.org/project/mtpx/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

MTPX is a protocol-first Python library for agent tool orchestration, built to support:
- Lazy tool loading by toolkit/category.
- Dependency-aware batch tool execution.
- Policy-aware execution based on tool risk.
- Multi-round model-tool-model loops.
- Provider adapters (now including Groq, Gemini, OpenAi, Anthropic, Openrouter, etc.).
- Transport primitives (stdio + HTTP + optional WebSocket envelope transport).
- Experimental MCP JSON-RPC adapter over the same runtime core.

## Direction

This project has two explicit layers:

- `MTP protocol`: protocol entities and execution semantics.
- `MTP Agent SDK`: framework/runtime/providers/toolkits/transports built on top of MTP.

MCP support is an interoperability capability, not the product identity.

Canonical direction document:
- [Project Direction](docs/PROJECT_DIRECTION.md)

## Quickstart

## Install

### From PyPI (recommended)

```bash
pip install mtpx
```

Common optional installs:

```bash
# Groq + dotenv helper
pip install "mtpx[groq,dotenv]"

# LM Studio local inference
pip install "mtpx[lmstudio]"

# Ollama local inference
pip install "mtpx[ollama]"

# OpenAI + Anthropic providers
pip install "mtpx[openai,anthropic,dotenv]"

# Web toolkits
pip install "mtpx[toolkits-web]"

# Database session stores
pip install "mtpx[stores-db]"

# Everything optional
pip install "mtpx[all]"
```

Verify installation:
```bash
python -c "import mtp; print(f'MTPX version {mtp.__version__} installed successfully!')"
```

### From source (for development)

```bash
git clone https://github.com/yourusername/MTP.git  # Replace with your actual repo URL
cd MTP
python -m venv .venv
.venv\Scripts\activate  # On Windows
# source .venv/bin/activate  # On Linux/Mac
pip install -e .
```

### Provider SDKs and dotenv (explicit alternative)

```bash
pip install "mtpx[groq,dotenv]"
```

Copy `.env.example` to `.env` and set your key:

```env
GROQ_API_KEY=your_groq_api_key_here
```

## Create an agent (local toolkits + Groq)

```python
from mtp import Agent
from mtp.providers import Groq
from mtp.toolkits import CalculatorToolkit, FileToolkit, PythonToolkit, ShellToolkit

Agent.load_dotenv_if_available()

tools = Agent.ToolRegistry()
tools.register_toolkit_loader("calculator", CalculatorToolkit())
tools.register_toolkit_loader("file", FileToolkit(base_dir="."))
tools.register_toolkit_loader("python", PythonToolkit(base_dir="."))
tools.register_toolkit_loader("shell", ShellToolkit(base_dir="."))

provider = Groq(model="llama-3.3-70b-versatile")

agent = Agent.MTPAgent(
    provider=provider,
    tools=tools,
    instructions="Use tools when needed and return concise answers.",
    debug_mode=True,
    strict_dependency_mode=True,
)
response = agent.run("Calculate 25*4+10 and list files in current directory.", max_rounds=4)
print(response)

# Stream final response tokens:
agent.print_response("Give me a short summary.", max_rounds=4, stream=True)

# Stream structured runtime events (readable terminal logs by default):
agent.print_response("Give me a short summary.", max_rounds=4, stream=True, stream_events=True)
# Raw JSON lines:
agent.print_response("Give me a short summary.", max_rounds=4, stream=True, stream_events=True, event_format="json")
```

### Autoresearch mode (persistent execution)

`autoresearch=True` enables persistent run behavior. The model is expected to end only when it calls `agent.terminate(...)`, or when user/system limits stop the run.

```python
agent = Agent.MTPAgent(
    provider=provider,
    tools=tools,
    autoresearch=True,
    research_instructions=(
        "Keep working until requirements are satisfied and verified. "
        "Call agent.terminate with reason+summary only when complete."
    ),
    debug_mode=True,
)

agent.print_response(
    "Compute the result and verify with tools. Terminate only after completion.",
    max_rounds=12,
    stream=True,
    stream_events=True,
)
```

## Persist conversation sessions (JSON database)

```python
from mtp import Agent, JsonSessionStore
from mtp.providers import OpenAI

session_store = JsonSessionStore(db_path="tmp/mtp_json_db")
agent = Agent.MTPAgent(provider=OpenAI(model="gpt-4o"), tools=tools, session_store=session_store)

agent.run("Remember this: project codename is Atlas.", session_id="chat-1", user_id="u1")
agent.run("What is the project codename?", session_id="chat-1", user_id="u1")
```

PostgreSQL and MySQL session stores are also available:

```python
from mtp import PostgresSessionStore, MySQLSessionStore

pg_store = PostgresSessionStore(db_url="postgresql://user:pass@localhost:5432/mtp")
my_store = MySQLSessionStore(
    host="localhost",
    user="root",
    password="secret",
    database="mtp",
    port=3306,
)
```

## Run examples

```bash
python examples/quickstart.py
python examples/groq_agent.py
python examples/groq_agent_events.py
python examples/mcp_stdio_server.py
```

```bash
pip install -e ".[groq,dotenv,ui-streamlit]"
streamlit run examples/streamlit_groq_agent_chat.py
```

```bash
pip install -e ".[dotenv,ui-streamlit,groq,openai,openrouter]"
mtp agent-os
```

## Docs map
- [Quickstart](docs/QUICKSTART.md)
- [Agent API Reference](docs/AGENT_API.md)
- [Storage and Sessions](docs/STORAGE.md)
- [Providers](docs/PROVIDERS.md)
- [Local Inference (LM Studio + Ollama)](docs/LOCAL_INFERENCE.md)
- [Creating Tools](docs/CREATING_TOOLS.md)
- [Events Contract](docs/EVENTS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Project Direction](docs/PROJECT_DIRECTION.md)
- [Protocol Spec](docs/PROTOCOL_SPEC.md)
- [Local Toolkits](docs/LOCAL_TOOLKITS.md)
- [Groq Integration](docs/GROQ_INTEGRATION.md)
- [Transport](docs/TRANSPORT.md)
- [MCP Interop Adapter](docs/MCP_INTEROP.md)
- [Publishing](docs/PUBLISHING.md)
- [Testing](docs/TESTING.md)

## Repository structure
- `src/mtp/protocol.py`: Core protocol entities (`ToolSpec`, `ToolCall`, `ExecutionPlan`, etc.).
- `src/mtp/schema.py`: Versioned envelope + execution plan validation.
- `src/mtp/policy.py`: Risk policy (`allow` / `ask` / `deny`).
- `src/mtp/runtime.py`: Tool registry, lazy loading, caching, batch execution.
- `src/mtp/agent.py`: Agent loop around provider + runtime.
- `src/mtp/toolkits/`: Local toolkits (`calculator`, `file`, `python`, `shell`).
- `src/mtp/transport/`: Envelope transport over stdio and HTTP.
- `src/mtp/mcp.py`: MCP-compatible JSON-RPC adapter around `ToolRegistry`.
- `src/mtp/providers/`: Provider adapters (`MockPlannerProvider` + OpenAI/LMStudio/Ollama/Groq/OpenRouter/Gemini/Anthropic/SambaNova/Cerebras/DeepSeek/Mistral/Cohere/TogetherAI/FireworksAI).
- `docs/`: documentation and implementation guides.

## Contributors

Created by [Prajwal Ghadge](mailto:prajwalghadge2005@gmail.com) with contributions from Himesh Mehta.

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for the full list of contributors.

## License

MIT License - see LICENSE file for details.
