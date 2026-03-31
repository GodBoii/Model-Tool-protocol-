# Quickstart

This guide shows how to create and run an MTP agent quickly.

## 1) Install

## From PyPI

```bash
pip install mtp
```

## Or from source

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Install provider and env helpers separately:

```bash
pip install groq
pip install python-dotenv
```

## 2) Configure API key

Create `.env`:

```env
GROQ_API_KEY=your_groq_api_key_here
```

## 3) Build your first agent

```python
from mtp import MTPAgent, ToolRegistry, load_dotenv_if_available
from mtp.providers import GroqToolCallingProvider
from mtp.toolkits import CalculatorToolkit, FileToolkit, PythonToolkit, ShellToolkit

load_dotenv_if_available()

registry = ToolRegistry()
registry.register_toolkit_loader("calculator", CalculatorToolkit())
registry.register_toolkit_loader("file", FileToolkit(base_dir="."))
registry.register_toolkit_loader("python", PythonToolkit(base_dir="."))
registry.register_toolkit_loader("shell", ShellToolkit(base_dir="."))

provider = GroqToolCallingProvider(
    model="llama-3.3-70b-versatile",
    strict_dependency_mode=True,
)

agent = MTPAgent(
    provider=provider,
    registry=registry,
    instructions="Use tools when useful and be concise.",
    debug_mode=True,
    strict_dependency_mode=True,
)

result = agent.run(
    "Calculate (25*4)+10 and list current directory files in one short summary.",
    max_rounds=4,
)
print(result)

# Or stream tokens to terminal:
agent.print_response(
    "Share a short summary of current directory files.",
    max_rounds=4,
    stream=True,
)

# Stream structured runtime events (JSON lines):
agent.print_response(
    "Calculate (25*4)+10 and list current directory files in one short summary.",
    max_rounds=4,
    stream=True,
    stream_events=True,
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

## 5) Next steps

- Add your own provider adapter under `src/mtp/providers/`
- Add your own toolkit under `src/mtp/toolkits/`
- Add a transport layer integration under `src/mtp/transport/`

## Strict dependency mode

When `strict_dependency_mode=True`, MTP enforces explicit dependency wiring for same-toolkit multi-call batches.

Example expectation:
- good: second call argument uses `{"$ref":"<tool_call_id>"}` or has `depends_on`
- rejected: second call hardcodes an inferred intermediate value
