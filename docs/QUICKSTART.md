# Quickstart

This guide shows how to create and run an MTP agent quickly.

## 1) Install

## From PyPI

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

# DB session stores
pip install "mtpx[stores-db]"
```

## Or from source

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Install provider and env helpers separately (equivalent):

```bash
pip install "mtpx[groq,dotenv]"
```

## 2) Configure API key

Create `.env`:

```env
GROQ_API_KEY=your_groq_api_key_here
```

## CLI bootstrap (optional)

You can scaffold a starter project instead of building manually:

```bash
mtp new my_agent --template minimal
cd my_agent
mtp run
```

See full CLI reference:
- [CLI](C:\Users\prajw\Downloads\MTP\docs\CLI.md)

## 3) Build your first agent

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

provider = Groq(model="llama-3.3-70b-versatile", strict_dependency_mode=True)

agent = Agent.MTPAgent(
    provider=provider,
    tools=tools,
    instructions="Use tools when useful and be concise.",
    debug_mode=True,
    strict_dependency_mode=True,
)

result = agent.run(
    "Calculate (25*4)+10 and list current directory files in one short summary.",
    max_rounds=4,
)
print(result)

# Async usage (inside existing asyncio apps):
# result = await agent.arun(
#     "Calculate (25*4)+10 and list current directory files in one short summary.",
#     max_rounds=4,
# )

# Or stream tokens to terminal:
agent.print_response(
    "Share a short summary of current directory files.",
    max_rounds=4,
    stream=True,
)

# Stream structured runtime events (readable terminal logs by default):
agent.print_response(
    "Calculate (25*4)+10 and list current directory files in one short summary.",
    max_rounds=4,
    stream=True,
    stream_events=True,
)

# For raw JSON lines:
agent.print_response(
    "Calculate (25*4)+10 and list current directory files in one short summary.",
    max_rounds=4,
    stream=True,
    stream_events=True,
    event_format="json",
)
```

## 4) Understand runtime behavior

`run`/`run_loop` does:
1. send messages + tool schemas to provider
2. provider returns direct text or tool plan
3. runtime executes tools (parallel/sequential by plan)
4. tool results are added back to conversation
5. loop continues until provider returns final text

Built-in MTP system instructions are appended automatically by the framework.
Your `instructions=` are added on top of those internal instructions.

Event stream includes:
- `run_started`
- `round_started`
- `plan_received`
- `batch_started`
- `tool_started`
- `tool_finished`
- `text_chunk`
- `run_completed`

Full schema:
- [Events Contract](C:\Users\prajw\Downloads\MTP\docs\EVENTS.md)
- [Agent API Reference](C:\Users\prajw\Downloads\MTP\docs\AGENT_API.md)

## 4.1) Autoresearch mode (optional)

Use autoresearch mode when you want persistent execution where the model should keep working until it explicitly terminates.

```python
agent = Agent.MTPAgent(
    provider=provider,
    tools=tools,
    autoresearch=True,
    research_instructions=(
        "Stay in persistent work mode. Validate with tools and call agent.terminate "
        "only after requirements are fully met."
    ),
    debug_mode=True,
)

agent.print_response(
    "Finish the task completely and terminate only when done.",
    max_rounds=12,
    stream=True,
    stream_events=True,
)
```

Notes:
- In autoresearch mode, direct assistant text is treated as intermediate progress (not immediate completion).
- Completion is expected via internal tool `agent.terminate(reason, summary)`.
- Event stream includes `run_terminated` before `run_completed` when the model terminates explicitly.

## 5) Next steps

- Enable persistent sessions:
  - [Storage and Sessions](C:\Users\prajw\Downloads\MTP\docs\STORAGE.md)
- Use local inference providers:
  - [Local Inference](C:\Users\prajw\Downloads\MTP\docs\LOCAL_INFERENCE.md)
- Add your own provider adapter under `src/mtp/providers/`
- Add your own toolkit under `src/mtp/toolkits/`
- Add a transport layer integration under `src/mtp/transport/`
- Build custom tools from Python functions:
  - [Creating Tools](C:\Users\prajw\Downloads\MTP\docs\CREATING_TOOLS.md)

## Strict dependency mode

When `strict_dependency_mode=True`, MTP enforces explicit dependency wiring for same-toolkit multi-call batches.

Example expectation:
- good: second call argument uses `{"$ref":"<tool_call_id>"}` or has `depends_on`
- rejected: second call hardcodes an inferred intermediate value

## Session persistence quick example

```python
from mtp import Agent, JsonSessionStore

store = JsonSessionStore(db_path="tmp/mtp_json_db")
agent = Agent.MTPAgent(provider=provider, tools=tools, session_store=store)

agent.run("Remember: project codename is Atlas.", session_id="dev-session", user_id="u1")
agent.run("What is the codename?", session_id="dev-session", user_id="u1")
```
