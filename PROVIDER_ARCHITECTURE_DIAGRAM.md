# MTP TUI Multi-Provider Architecture Diagram

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MTP TUI CLI Application                            │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         User Interface Layer                         │   │
│  │                                                                       │   │
│  │  • Prompt Input (prompt_toolkit or fallback)                        │   │
│  │  • Command Parser (/backend, /model, /help, etc.)                   │   │
│  │  • Response Renderer (markdown, code blocks, tool events)           │   │
│  │  • Status Display (model, backend, session info)                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                          │
│                                    ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        Command Router                                │   │
│  │                                                                       │   │
│  │  /backend <provider>  ──────────▶  Backend Switcher                 │   │
│  │  /model <name>        ──────────▶  Model Selector                   │   │
│  │  /model add <name>    ──────────▶  Model Manager                    │   │
│  │  <chat message>       ──────────▶  Chat Executor                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                          │
│                                    ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      Backend Dispatcher                              │   │
│  │                                                                       │   │
│  │  if backend == "codex":                                              │   │
│  │      ──▶ Codex Backend Module                                        │   │
│  │  else:                                                                │   │
│  │      ──▶ MTP Provider Backend Module                                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                          │
│                    ┌───────────────┴───────────────┐                         │
│                    ▼                               ▼                         │
│  ┌──────────────────────────────┐   ┌──────────────────────────────┐       │
│  │   Codex Backend Module       │   │   MTP Provider Backend       │       │
│  │   (tui_codex_backend.py)     │   │   (tui_mtp_backend.py)       │       │
│  │                               │   │                               │       │
│  │  • Binary detection           │   │  • Provider initialization    │       │
│  │  • Login flow                 │   │  • Agent creation             │       │
│  │  • Session management         │   │  • Event streaming            │       │
│  │  • Sandbox modes              │   │  • Tool execution             │       │
│  │  • Reasoning effort           │   │  • Usage metrics              │       │
│  │  • Command construction       │   │                               │       │
│  └──────────────────────────────┘   └──────────────────────────────┘       │
│                │                                     │                        │
│                ▼                                     ▼                        │
│  ┌──────────────────────────────┐   ┌──────────────────────────────┐       │
│  │   External Codex CLI         │   │   MTP Provider Factory       │       │
│  │   (codex.cmd / codex)        │   │   (tui_provider_factory.py)  │       │
│  │                               │   │                               │       │
│  │  • OpenAI's binary           │   │  • Provider builders          │       │
│  │  • Subprocess execution       │   │  • API key management         │       │
│  │  • JSON event streaming       │   │  • Model selection            │       │
│  └──────────────────────────────┘   └──────────────────────────────┘       │
│                                                     │                        │
│                                                     ▼                        │
│                                      ┌──────────────────────────────┐       │
│                                      │   MTP SDK Providers          │       │
│                                      │   (src/mtp/providers/)       │       │
│                                      │                               │       │
│                                      │  • OpenAI                     │       │
│                                      │  • Groq                       │       │
│                                      │  • Anthropic (Claude)         │       │
│                                      │  • Gemini                     │       │
│                                      │  • OpenRouter                 │       │
│                                      │  • Mistral                    │       │
│                                      │  • Cohere                     │       │
│                                      │  • SambaNova                  │       │
│                                      │  • Cerebras                   │       │
│                                      │  • DeepSeek                   │       │
│                                      │  • TogetherAI                 │       │
│                                      │  • FireworksAI                │       │
│                                      └──────────────────────────────┘       │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      Persistence Layer                               │   │
│  │                                                                       │   │
│  │  • Session Store (JsonSessionStore)                                  │   │
│  │  • Provider Settings (tui_provider_settings.json)                    │   │
│  │  • Conversation History                                              │   │
│  │  • Tool Registry                                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Provider Switching Flow

```
User: /backend groq
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. Validate provider name                                     │
│    ✓ "groq" in SUPPORTED_TUI_PROVIDERS                        │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. Load provider settings                                     │
│    • Read tui_provider_settings.json                          │
│    • Check if "groq" entry exists                             │
└──────────────────────────────────────────────────────────────┘
       │
       ├─── API key missing? ───▶ ┌────────────────────────────┐
       │                           │ Interactive Setup Flow     │
       │                           │ • Prompt for API key       │
       │                           │ • Prompt for model         │
       │                           │ • Save settings            │
       │                           └────────────────────────────┘
       │                                      │
       ▼◀─────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ 3. Build provider instance                                    │
│    • ProviderSelection(provider="groq", model="...", key=...) │
│    • build_tui_provider(selection)                            │
│    • Returns Groq(model="...", api_key="...")                 │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. Initialize MTP Agent                                       │
│    • Agent.MTPAgent(provider=groq_provider, tools=registry)   │
│    • Configure autoresearch, debug_mode, etc.                 │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. Update TUI state                                           │
│    • state.backend = "groq"                                   │
│    • state.agent = new_agent                                  │
│    • state.active_provider_model = "llama-3.3-70b-versatile" │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 6. Save session                                               │
│    • _save_tui_session(state)                                 │
│    • Persist backend + model to session metadata              │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 7. Display confirmation                                       │
│    ✓ Switched to groq with model llama-3.3-70b-versatile     │
└──────────────────────────────────────────────────────────────┘
```

---

## Chat Execution Flow

```
User: "Explain quantum computing"
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. Parse input                                                │
│    • Detect @file attachments                                 │
│    • Expand file contents                                     │
│    • Build final prompt                                       │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. Route to backend                                           │
│    if state.backend == "codex":                               │
│        ──▶ _execute_codex_chat()                              │
│    else:                                                       │
│        ──▶ _execute_mtp_chat()                                │
└──────────────────────────────────────────────────────────────┘
       │
       ├─── Codex Backend ───▶ ┌────────────────────────────────┐
       │                        │ • Build codex exec command     │
       │                        │ • Run subprocess               │
       │                        │ • Parse JSON events            │
       │                        │ • Extract tool calls           │
       │                        │ • Extract usage metrics        │
       │                        └────────────────────────────────┘
       │
       └─── MTP Backend ────▶ ┌────────────────────────────────┐
                               │ • state.agent.run_loop()       │
                               │ • Execute tool calls           │
                               │ • Stream events (optional)     │
                               │ • Extract usage metrics        │
                               └────────────────────────────────┘
                                      │
                                      ▼
                               ┌────────────────────────────────┐
                               │ ChatResult                     │
                               │ • text: final response         │
                               │ • tool_events: list of tools   │
                               │ • attachments: file refs       │
                               │ • warnings: errors/issues      │
                               │ • usage_lines: token counts    │
                               └────────────────────────────────┘
                                      │
                                      ▼
                               ┌────────────────────────────────┐
                               │ Render Response                │
                               │ • Format markdown              │
                               │ • Show tool events             │
                               │ • Display usage bar            │
                               │ • Show warnings                │
                               └────────────────────────────────┘
```

---

## Model Management Flow

```
User: /model add gpt-4o-2024-08-06
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. Parse command                                              │
│    • Extract "add" subcommand                                 │
│    • Extract model name: "gpt-4o-2024-08-06"                  │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. Validate backend                                           │
│    • Check state.backend != "codex"                           │
│    • Codex doesn't support custom models                      │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. Load provider settings                                     │
│    • Read tui_provider_settings.json                          │
│    • Get entry for current provider                           │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. Add model to list                                          │
│    • entry["models"].append("gpt-4o-2024-08-06")              │
│    • Check for duplicates                                     │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. Save settings                                              │
│    • save_provider_settings(path, settings)                   │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 6. Display confirmation                                       │
│    ✓ Added model gpt-4o-2024-08-06 to openai                 │
└──────────────────────────────────────────────────────────────┘

User: /model gpt-4o-2024-08-06
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. Resolve model name                                         │
│    • Check shortcuts (1-4)                                    │
│    • Use literal name if not shortcut                         │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. Update provider settings                                   │
│    • entry["model"] = "gpt-4o-2024-08-06"                     │
│    • save_provider_settings()                                 │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. Rebuild agent                                              │
│    • state.agent = None (force rebuild)                       │
│    • Next chat will reinitialize with new model               │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. Display confirmation                                       │
│    ✓ Model set to gpt-4o-2024-08-06. Agent reloaded.         │
└──────────────────────────────────────────────────────────────┘
```

---

## Provider Settings Storage Schema

```json
{
  "providers": {
    "openai": {
      "api_key": "sk-proj-...",
      "model": "gpt-4o",
      "models": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4o-2024-08-06"
      ]
    },
    "groq": {
      "api_key": "gsk_...",
      "model": "llama-3.3-70b-versatile",
      "models": [
        "llama-3.3-70b-versatile",
        "mixtral-8x7b-32768"
      ]
    },
    "claude": {
      "api_key": "sk-ant-...",
      "model": "claude-3-5-sonnet-20241022",
      "models": []
    },
    "gemini": {
      "api_key": "AIza...",
      "model": "gemini-2.0-flash",
      "models": [
        "gemini-2.0-flash",
        "gemini-1.5-pro"
      ]
    }
  }
}
```

**Storage Location**: `{session_db_path}/tui_provider_settings.json`

**Default Location**: `tmp/mtp_json_db/tui_provider_settings.json`

---

## Session Metadata Schema

```json
{
  "session_id": "chat-a1b2c3d4e5",
  "user_id": "tui-user",
  "metadata": {
    "tui": {
      "session_label": "My Project Chat",
      "backend": "groq",
      "cwd": "/home/user/project",
      "codex_model": null,
      "openai_model": "gpt-4o",
      "active_provider_model": "llama-3.3-70b-versatile",
      "codex_session_id": null,
      "reasoning_effort": "medium",
      "codex_sandbox_mode": "workspace-write",
      "max_rounds": 6,
      "autoresearch": false,
      "research_instructions": null,
      "last_usage_lines": [
        "tokens(in/out/total/reasoning)=1234/567/1801/0",
        "context_window=1801/128000 used (1.41%)"
      ],
      "turn_count": 5,
      "updated_at": "2026-04-16 14:30:00",
      "transcript": [
        {
          "prompt": "Explain quantum computing",
          "response": "Quantum computing is...",
          "backend": "groq",
          "model": "llama-3.3-70b-versatile",
          "attachments": [],
          "warnings": [],
          "usage_lines": ["tokens(in/out/total/reasoning)=1234/567/1801/0"],
          "created_at": "2026-04-16 14:25:00"
        }
      ]
    }
  },
  "messages": [],
  "runs": [],
  "created_at": "2026-04-16 14:00:00",
  "updated_at": "2026-04-16 14:30:00"
}
```

---

## Command Reference

### Backend Management

| Command | Description | Example |
|---------|-------------|---------|
| `/backend` | List all providers with status | `/backend` |
| `/backend <provider>` | Switch to provider | `/backend groq` |
| `/backend codex` | Switch to Codex CLI | `/backend codex` |

### Model Management

| Command | Description | Example |
|---------|-------------|---------|
| `/models` | Show available models | `/models` |
| `/model <name>` | Switch to model | `/model gpt-4o` |
| `/model 1-4` | Use preset shortcut | `/model 2` |
| `/model add <name>` | Add custom model | `/model add gpt-4o-2024-08-06` |
| `/model default` | Reset to default | `/model default` |

### Provider Setup

When switching to an unconfigured provider:

```
User: /backend groq

  Setup groq
  API key not found. Please provide your API key.
  ▸ API Key: gsk_...

  Model Selection
  Default: llama-3.3-70b-versatile
  ▸ Model (press Enter for default): 

✓ Provider groq configured successfully!
✓ Switched to groq with model llama-3.3-70b-versatile.
```

---

## Provider Comparison Matrix

| Feature | Codex Backend | MTP Provider Backend |
|---------|---------------|----------------------|
| **Execution** | External binary (subprocess) | In-process (Python SDK) |
| **Authentication** | `codex login` flow | API key in settings |
| **Session Management** | Thread ID tracking | MTP session store |
| **Tool Execution** | Codex-managed | MTP runtime |
| **Streaming** | JSON event stream | MTP event stream |
| **Sandbox Modes** | ✅ (read-only, workspace-write, full) | ❌ (not applicable) |
| **Reasoning Effort** | ✅ (none, low, medium, high, xhigh) | ❌ (provider-specific) |
| **Model Switching** | ✅ (via -m flag) | ✅ (via provider config) |
| **Custom Models** | ❌ (Codex presets only) | ✅ (any model name) |
| **Offline Mode** | ❌ (requires Codex binary) | ❌ (requires API access) |
| **Multi-Provider** | ❌ (OpenAI only) | ✅ (13 providers) |

---

## Error Handling

### Provider Not Found

```
User: /backend unknown-provider

✗ Unknown provider: unknown-provider
  Available providers: codex, openai, groq, claude, gemini, ...
  Usage: /backend <provider>
```

### API Key Missing

```
User: /backend groq

  Setup groq
  API key not found. Please provide your API key.
  ▸ API Key: [user cancels]

✗ Setup cancelled.
```

### Provider Initialization Failed

```
User: /backend openai

✗ Failed to initialize provider: Invalid API key

  Troubleshooting:
  • Check your API key in settings
  • Run /backend openai to reconfigure
  • Verify API key at https://platform.openai.com/api-keys
```

### Model Not Available

```
User: /model gpt-5

⚠ Model gpt-5 not found for openai
  Available models:
    • gpt-4o (default)
    • gpt-4o-mini
    • gpt-4o-2024-08-06

  Usage: /model <name> or /model add <name>
```

---

## Future Enhancements

### 1. Provider Health Check

```
/backend status

┌─ Provider Status ─────────────────────────────────────────┐
│                                                            │
│  ● codex          ✓ Ready      (binary found)             │
│  ● openai         ✓ Ready      (API key valid)            │
│  ● groq           ✓ Ready      (API key valid)            │
│  ○ claude         ⚠ Not setup  (API key missing)          │
│  ● gemini         ✗ Error      (Invalid API key)          │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 2. Provider Benchmarking

```
/benchmark "Explain quantum computing"

Running benchmark across 3 providers...

┌─ Benchmark Results ───────────────────────────────────────┐
│                                                            │
│  Provider    Response Time    Tokens    Cost              │
│  ─────────   ─────────────    ──────    ────              │
│  groq        1.2s              1,234     $0.001           │
│  openai      2.5s              1,456     $0.015           │
│  claude      3.1s              1,389     $0.020           │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 3. Provider Profiles

```
/profile save work
✓ Saved current settings as profile "work"

/profile load work
✓ Loaded profile "work" (backend: openai, model: gpt-4o)

/profile list
  • work (openai, gpt-4o)
  • personal (groq, llama-3.3-70b)
  • research (claude, claude-3-5-sonnet)
```

### 4. Multi-Provider Consensus

```
/consensus "Is this code correct?" @file.py

Querying 3 providers for consensus...

┌─ Consensus Results ───────────────────────────────────────┐
│                                                            │
│  ✓ 2/3 providers agree: Code is correct                   │
│                                                            │
│  openai:  ✓ Correct                                       │
│  groq:    ✓ Correct                                       │
│  claude:  ✗ Potential bug in line 42                      │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

---

## Conclusion

This architecture provides:

1. **Unified Interface**: Single TUI for all providers
2. **Seamless Switching**: Easy provider/model changes
3. **Persistent Settings**: API keys and preferences saved
4. **Backward Compatibility**: Existing Codex workflows preserved
5. **Extensibility**: Easy to add new providers
6. **User-Friendly**: Interactive setup flows
7. **Flexible**: Custom models and configurations

The design maintains the excellent UX of the current Codex-focused TUI while opening it up to the entire MTP provider ecosystem.
