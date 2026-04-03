# MTP Python

MTP is a protocol-first Python library for agent tool orchestration, built to support:
- Lazy tool loading by toolkit/category.
- Dependency-aware batch tool execution.
- Policy-aware execution based on tool risk.
- Multi-round model-tool-model loops.
- Provider adapters (now including Groq).
- Transport primitives (stdio + HTTP envelope transport).

## Quickstart

## Install

### From source (this repo)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

### From PyPI (target usage)

```bash
pip install mtp
```

### Provider SDKs and dotenv (install separately)

```bash
pip install groq
pip install python-dotenv
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
```

## Docs map
- [Quickstart](C:\Users\prajw\Downloads\MTP\docs\QUICKSTART.md)
- [Agent API Reference](C:\Users\prajw\Downloads\MTP\docs\AGENT_API.md)
- [Storage and Sessions](C:\Users\prajw\Downloads\MTP\docs\STORAGE.md)
- [Providers](C:\Users\prajw\Downloads\MTP\docs\PROVIDERS.md)
- [Creating Tools](C:\Users\prajw\Downloads\MTP\docs\CREATING_TOOLS.md)
- [Events Contract](C:\Users\prajw\Downloads\MTP\docs\EVENTS.md)
- [Architecture](C:\Users\prajw\Downloads\MTP\docs\ARCHITECTURE.md)
- [Protocol Spec](C:\Users\prajw\Downloads\MTP\docs\PROTOCOL_SPEC.md)
- [Local Toolkits](C:\Users\prajw\Downloads\MTP\docs\LOCAL_TOOLKITS.md)
- [Groq Integration](C:\Users\prajw\Downloads\MTP\docs\GROQ_INTEGRATION.md)
- [Transport](C:\Users\prajw\Downloads\MTP\docs\TRANSPORT.md)
- [Publishing](C:\Users\prajw\Downloads\MTP\docs\PUBLISHING.md)

## Repository structure
- `src/mtp/protocol.py`: Core protocol entities (`ToolSpec`, `ToolCall`, `ExecutionPlan`, etc.).
- `src/mtp/schema.py`: Versioned envelope + execution plan validation.
- `src/mtp/policy.py`: Risk policy (`allow` / `ask` / `deny`).
- `src/mtp/runtime.py`: Tool registry, lazy loading, caching, batch execution.
- `src/mtp/agent.py`: Agent loop around provider + runtime.
- `src/mtp/toolkits/`: Local toolkits (`calculator`, `file`, `python`, `shell`).
- `src/mtp/transport/`: Envelope transport over stdio and HTTP.
- `src/mtp/providers/`: Provider adapters (`MockPlannerProvider` + OpenAI/Groq/OpenRouter/Gemini/Anthropic/SambaNova/Cerebras/DeepSeek/Mistral/Cohere/TogetherAI/FireworksAI).
- `docs/`: documentation and implementation guides.
