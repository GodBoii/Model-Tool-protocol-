# MTP SDK Cookbook 🧑‍🍳

Welcome to the MTP (Model-Tool Protocol) SDK Cookbook! This guide provides practical, modular "recipes" for building powerful AI agents using the MTP framework.

## Table of Contents
1. [Basic Recipes](#basic-recipes)
    - [Hello World Agent](#hello-world-agent)
    - [Creating a Custom Tool (Function)](#creating-a-custom-tool-function)
    - [Using Built-in Toolkits](#using-built-in-toolkits)
2. [Advanced Agent Patterns](#advanced-agent-patterns)
    - [Multi-Round Autoresearch](#multi-round-autoresearch)
    - [Streaming Response Tokens](#streaming-response-tokens)
    - [Observing Internal Events](#observing-internal-events)
3. [Providers & Model Management](#providers--model-management)
    - [Switching Providers (Cloud vs Local)](#switching-providers-cloud-vs-local)
    - [Local Inference with Ollama/LM Studio](#local-inference-with-ollama-lm-studio)
4. [Persistence & Sessions](#persistence--sessions)
    - [JSON Database Session Store](#json-database-session-store)
    - [PostgreSQL Session Store](#postgresql-session-store)
5. [Interoperability (MCP)](#interoperability-mcp)
    - [Connecting to MCP Servers](#connecting-to-mcp-servers)

---

## Basic Recipes

### Hello World Agent
The simplest way to get started with MTP.

```python
from mtp import Agent
from mtp.providers import Groq

# Load keys from .env
Agent.load_dotenv_if_available()

# Initialize tools registry
tools = Agent.ToolRegistry()

# Setup provider
provider = Groq(model="llama-3.3-70b-versatile")

# Create agent
agent = Agent.MTPAgent(provider=provider, tools=tools)

# Run
response = agent.run("Hello! Who are you?")
print(response)
```

### Creating a Custom Tool (Function)
Turn any Python function into a tool the model can use.

```python
from mtp import Agent, mtp_tool

@mtp_tool(description="Get the current weather for a location")
def get_weather(location: str) -> str:
    # In a real app, call a weather API
    return f"The weather in {location} is sunny and 25°C."

tools = Agent.ToolRegistry()
# Register the tool directly
tools.add_tool(get_weather)

agent = Agent.MTPAgent(provider=provider, tools=tools)
print(agent.run("What's the weather like in Paris?"))
```

### Using Built-in Toolkits
MTP comes with several ready-to-use toolkits.

```python
from mtp import Agent
from mtp.toolkits import CalculatorToolkit, ShellToolkit

tools = Agent.ToolRegistry()
tools.register_toolkit_loader("calc", CalculatorToolkit())
tools.register_toolkit_loader("sh", ShellToolkit(base_dir="."))

agent = Agent.MTPAgent(provider=provider, tools=tools)
agent.print_response("Calculate (45 * 12) + 120 and list files here.")
```

---

## Advanced Agent Patterns

### Multi-Round Autoresearch
Enable the agent to work persistently until a task is completed.

```python
agent = Agent.MTPAgent(
    provider=provider,
    tools=tools,
    autoresearch=True,
    research_instructions="Verify all facts with tools before finishing."
)

# Max rounds allows the agent to loop multiple times
agent.print_response("Research the latest news about MTPX and summarize.", max_rounds=10)
```

### Streaming Response Tokens
Provide a better UX by showing tokens as they are generated.

```python
agent = Agent.MTPAgent(provider=provider, tools=tools)

# Use print_response with stream=True
agent.print_response("Write a long poem about coding.", stream=True)

# Or use the run_stream iterator for custom UIs
for chunk in agent.run_stream("Tell me a story."):
    print(chunk, end="", flush=True)
```

### Observing Internal Events
Monitor tool calls, reasoning, and metrics in real-time.

```python
# Stream pretty-printed events to console
agent.print_response("Do some math.", stream_events=True)

# Or capture raw JSON events for your application logic
for event in agent.run_events("Search for 'Python SDK'"):
    if event["type"] == "tool_started":
        print(f"🛠️ Starting tool: {event['tool_name']}")
    elif event["type"] == "tool_completed":
        print(f"✅ Tool result: {event['output']}")
```

---

## Providers & Model Management

### Switching Providers (Cloud vs Local)
MTP supports a wide range of providers with a unified interface.

```python
from mtp.providers import OpenAI, Anthropic, Gemini, Groq, Ollama

# Cloud Providers
openai_provider = OpenAI(model="gpt-4o")
anthropic_provider = Anthropic(model="claude-3-5-sonnet-latest")

# Local Provider
ollama_provider = Ollama(model="llama3")

# Just swap the provider in the MTPAgent constructor
agent = Agent.MTPAgent(provider=ollama_provider, tools=tools)
```

### Local Inference with Ollama/LM Studio
Run your agents entirely on your machine.

```python
from mtp.providers import Ollama, LMStudio

# Ollama (requires Ollama running locally)
provider = Ollama(model="llama3")

# LM Studio (requires LM Studio 'Local Server' running)
# provider = LMStudio(model="path/to/your/model")

agent = Agent.MTPAgent(provider=provider, tools=tools)
agent.run("Tell me something interesting about local LLMs.")
```

---

## Persistence & Sessions

### JSON Database Session Store
Persist conversation history across restarts using a simple JSON file.

```python
from mtp import Agent, JsonSessionStore

store = JsonSessionStore(db_path="data/my_sessions")
agent = Agent.MTPAgent(provider=provider, tools=tools, session_store=store)

# History is automatically saved and retrieved using session_id
agent.run("My name is Alice.", session_id="user-123")
agent.run("What is my name?", session_id="user-123") # Returns "Your name is Alice."
```

### PostgreSQL Session Store
For production multi-user applications.

```python
from mtp import PostgresSessionStore

pg_store = PostgresSessionStore(db_url="postgresql://user:pass@localhost:5432/mtp_db")
agent = Agent.MTPAgent(provider=provider, tools=tools, session_store=pg_store)
```

---

## Interoperability (MCP)

### Connecting to MCP Servers
MTP can act as an MCP client, allowing you to use existing MCP servers as toolkits.

```python
# MTP supports MCP natively. You can run an MCP server and connect to it.
# Example: Using the MTP MCP adapter
from mtp import run_mcp_stdio
```

> [!TIP]
> For more detailed examples, check the `examples/` directory in the repository!
