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
from mtp import Agent, ToolRegistry, load_dotenv_if_available, register_local_toolkits
from mtp.providers import GroqToolCallingProvider

load_dotenv_if_available()

registry = ToolRegistry()
register_local_toolkits(registry, base_dir=".")

provider = GroqToolCallingProvider(
    model="llama-3.3-70b-versatile",
    system_prompt="Use tools when useful and be concise.",
    strict_dependency_mode=True,
)

agent = Agent(
    provider=provider,
    registry=registry,
    debug_mode=True,  # prints tool plan + tool results
    strict_dependency_mode=True,
)

result = agent.run_loop(
    "Calculate (25*4)+10 and list current directory files in one short summary.",
    max_rounds=4,
)
print(result)
```

## 4) Understand runtime behavior

`run_loop` does:
1. send messages + tool schemas to provider
2. provider returns direct text or tool plan
3. runtime executes tools (parallel/sequential by plan)
4. tool results are added back to conversation
5. loop continues until provider returns final text

## 5) Next steps

- Add your own provider adapter under `src/mtp/providers/`
- Add your own toolkit under `src/mtp/toolkits/`
- Add a transport layer integration under `src/mtp/transport/`

## Strict dependency mode

When `strict_dependency_mode=True`, MTP enforces explicit dependency wiring for same-toolkit multi-call batches.

Example expectation:
- good: second call argument uses `{"$ref":"<tool_call_id>"}` or has `depends_on`
- rejected: second call hardcodes an inferred intermediate value
