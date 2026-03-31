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
from mtp import MTPAgent, ToolRegistry, load_dotenv_if_available
from mtp.providers import GroqToolCallingProvider
from mtp.toolkits import CalculatorToolkit, FileToolkit, PythonToolkit, ShellToolkit

load_dotenv_if_available()

registry = ToolRegistry()
registry.register_toolkit_loader("calculator", CalculatorToolkit())
registry.register_toolkit_loader("file", FileToolkit(base_dir="."))
registry.register_toolkit_loader("python", PythonToolkit(base_dir="."))
registry.register_toolkit_loader("shell", ShellToolkit(base_dir="."))

provider = GroqToolCallingProvider(model="llama-3.3-70b-versatile")

agent = MTPAgent(
    provider=provider,
    registry=registry,
    instructions="Use tools when needed and return concise answers.",
    debug_mode=True,
    strict_dependency_mode=True,
)
response = agent.run("Calculate 25*4+10 and list files in current directory.", max_rounds=4)
print(response)

# Stream final response tokens:
agent.print_response("Give me a short summary.", max_rounds=4, stream=True)

# Stream structured runtime events (JSON lines):
agent.print_response("Give me a short summary.", max_rounds=4, stream=True, stream_events=True)
```

## Run examples

```bash
python examples/quickstart.py
python examples/groq_agent.py
```

## Docs map
- [Quickstart](C:\Users\prajw\Downloads\MTP\docs\QUICKSTART.md)
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
- `src/mtp/providers/`: Provider adapters (`MockPlannerProvider`, `GroqToolCallingProvider`).
- `docs/`: documentation and implementation guides.
