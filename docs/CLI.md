# CLI

MTP provides a first-party CLI:

```bash
mtp --help
```

## Commands

## `mtp new <name>`

Create a new project scaffold.

```bash
mtp new my_agent
mtp new my_server --template mcp-http
mtp new my_memory_agent --template session-json
```

Options:
- `--template {minimal,mcp-http,session-json}`
- `--dir <base_dir>`
- `--force`

Generated projects include:
- starter code (`app.py` or `server.py`)
- `.env.example`
- `pyproject.toml` with optional provider extras suggestions
- `README.md`

## `mtp run`

Run a scaffolded project entry script from the current folder (or `--path`).

```bash
mtp run
mtp run --path ./my_agent
mtp run --path ./my_server --entry server.py
```

Default entry resolution order:
1. `app.py`
2. `server.py`
3. `main.py`

## `mtp doctor`

Environment validation tool.

```bash
mtp doctor
mtp doctor --provider groq
mtp doctor --provider openai --provider anthropic
```

Checks include:
- Python version support
- `python-dotenv` availability
- provider SDK import availability
- provider API key environment variable presence

Returns non-zero when a check fails. Warnings remain visible but do not make the
command fail, which keeps optional provider SDKs from breaking health checks.

## `mtp providers list`

List known providers and their operational metadata.

```bash
mtp providers list
```

Output columns:
- provider name
- alias/class
- SDK module and install status
- API key env var
- API key configuration status and overall readiness

Credential values are never printed. To inspect one provider by name, alias, or
provider class name:

```bash
mtp providers show groq
mtp providers show OpenAI --json
```

The `ready` field means the provider's Python SDK is importable and its required
API-key environment variable is non-empty. Local and built-in providers show
`not-required` when no API key is needed. This is a local configuration check;
it does not make a network request or validate the credential with the provider.

## `mtp codebase memory`

Enable or disable project codebase memory and indexing.

```bash
mtp codebase memory
mtp codebase memory --on
mtp codebase memory --off
mtp codebase memory --path ./my_project --on
mtp codebase status
```

When enabled, MTP scans the project root, skips heavy/generated folders such as
`.git`, `.venv`, `venv`, `node_modules`, `dist`, `build`, caches, and secret-like
files, then stores the index in:

```text
<project>/.mtp/memory/codebase.sqlite
```

The index includes file metadata, code/text chunks, deterministic vector-style
embeddings for semantic matching, and conversation summaries recorded after TUI
turns. TUI users can manage the same feature with:

```text
/codebase memory
/codebase memory on
/codebase memory off
/codebase status
```

When memory is on, TUI harness tools such as `project.inspect`, `fs.search`,
`fs.grep`, and `codebase.search` use the stored index and refresh changed files
before retrieval.

## `mtp sessions`

Inspect, transfer, and remove sessions in a JSON session database:

```bash
mtp sessions list --json
mtp sessions show chat-123 --user-id alice --json
mtp sessions export backup.json --user-id alice
mtp sessions export one.json --session-id chat-123 --user-id alice
mtp sessions import backup.json --user-id alice
mtp sessions delete chat-123 --user-id alice --yes
```

`show` requires `--user-id` when the same session ID exists for multiple
owners. The user filter is always an exact owner match; it never changes session
ownership during import.

Exports use a versioned JSON Schema envelope documented in
`docs/schemas/session-export-v1.schema.json`. MTP validates the complete bundle
before importing anything. Export publication and database import are atomic,
and both refuse to replace existing data by default. Use `--force` deliberately
to replace an export file or sessions with the same `(session_id, user_id)`
identity. Existing sessions owned by other users are not affected.

## `mtp tui`

Launch the interactive terminal UI.

```bash
mtp tui
```

Recommended launch command:
- `mtp tui` (single top-level command, consistent with existing CLI)

### Features

**Multi-Provider Support**:
- Cloud providers: OpenAI, Anthropic, Google Gemini, Groq, etc.
- Local providers: Ollama, LM Studio
- Switch providers with `/backend <provider>`

**Metrics Display**:
- Context window usage with progress bar
- Token metrics (input/output/total/reasoning)
- Performance metrics (speed, duration, LLM calls)
- Thinking tokens for supported models (Ollama)

**Session Management**:
- Persistent chat sessions with auto-generated titles
- Centralized session storage in `~/.mtp/sessions/`
- Directory-based session grouping
- Session history and replay
- Multi-turn conversations with context
- Sessions accessible from any directory

**Session Commands:**
- `/sessions` - List all saved sessions (grouped by directory)
- `/new [label]` - Start a new session (optional custom label)
- `/load <session_id>` - Load a saved session
- `/open <session_id>` - View a session (read-only)
- `/history [n]` - Show recent turns in current session

**Session Features:**
- **Auto-Generated Titles**: Session titles are automatically created from your first message
  - Example: "How do I implement authentication" → Title: "How do I implement"
  - File attachments (@file) are removed from titles
  - Very short prompts default to "Quick chat"
- **Centralized Storage**: All sessions stored in `~/.mtp/sessions/` regardless of working directory
- **Directory Grouping**: Sessions organized by project directory when listed
  - Current directory sessions shown first (marked with ●)
  - Other directories shown below (marked with ○)
- **Cross-Project Access**: Resume sessions from any directory
- **Persistent**: Sessions survive even if project directories are deleted

**Session List Example:**
```
Saved Sessions
──────────────────────────────────────────────────────────

● webapp (current directory)
  abc123 Fix authentication bug
    openai • 5 turns • 2026-04-17 10:30:00
  def456 Implement user login
    groq • 3 turns • 2026-04-17 11:00:00

○ api-service
  ghi789 Debug API endpoint
    claude • 7 turns • 2026-04-17 09:15:00
```

**Modern UI/UX & Aesthetics**:
- **Interactive Cat Companion**: An animated terminal cat that tracks your input cursor and reacts to generation states.
- **Nerd Font Acceleration**: High-fidelity icon support for specialized developer fonts.
- **Phosphor Decay**: "Hot ink" typewriter effect where streaming text appears bright before "cooling" into the layout.
- **Dynamic Feedback**: Input pulse animations and smooth toast transitions for tactile responsiveness.
- **Telemetry HUD**: Right-gutter sidebar displaying CWD, Sandbox mode, and attachment metrics.

### Provider Setup

When switching to a new MTP provider for the first time, TUI will prompt for:
1. **API Key**: Enter your provider API key (validated to prevent masked keys)
2. **Model Selection**: Choose a model or press Enter for default

API keys are stored in your operating system's credential vault (Windows
Credential Manager, macOS Keychain, or the configured Linux Secret Service),
not in `provider_settings.json`. The JSON file contains only non-secret choices
such as models and endpoints. MTPX also reads each provider's standard
environment variable (for example `GROQ_API_KEY`) when no vault entry exists.

The `/apikey set` command fails with an actionable environment-variable hint if
no usable keyring backend is available; it never falls back to plaintext.
Existing plaintext settings are migrated automatically and removed from JSON
only after the vault confirms the write. If migration cannot run, MTPX warns and
keeps the original file unchanged so the only copy is not lost.

Supported providers and their default models:
- **openai**: `gpt-4o`
- **groq**: `llama-3.3-70b-versatile`
- **ollama**: Auto-discovered local models
- **lmstudio**: Auto-discovered local models

### Metrics Display

The TUI displays comprehensive metrics after each response:

**Context Window**:
```
ctx [████████████████░░░░] 32,768/131,072 (25%)
```

**Thinking Tokens** (Ollama with supported models):
```
💭 thinking Let me calculate this step by step: 2 + 2 = 4
```

**Token Metrics**:
```
tokens(in/out/total/reasoning)=150/50/200/30
```

**Performance**:
```
llm_calls=1  duration=1.23s  speed=162.6 tokens/s
```

See [TUI Local Inference Guide](TUI_LOCAL_INFERENCE.md) for detailed metrics documentation.
- **claude**: `claude-sonnet-4-6`
- **gemini**: `gemini-3.5-flash`
- **openrouter**: `openai/gpt-4o`
- **mistral**: `mistral-large-latest`
- **cohere**: `command-r-plus`
- **sambanova**: `Meta-Llama-3.1-70B-Instruct`
- **cerebras**: `llama3.1-70b`
- **deepseek**: `deepseek-chat`
- **togetherai**: `meta-llama/Llama-3.3-70B-Instruct-Turbo`
- **fireworksai**: `accounts/fireworks/models/llama-v3p1-70b-instruct`

Backends:
- `codex`: bridges to official Codex CLI (`codex exec`) and uses your Codex login session.
- **MTP Providers** (12 supported): `openai`, `groq`, `claude`, `gemini`, `openrouter`, `mistral`, `cohere`, `sambanova`, `cerebras`, `deepseek`, `togetherai`, `fireworksai`
  - Each uses MTP SDK with the respective provider and local toolkits
  - Requires provider API key configuration
  - Supports custom model selection and management

Codex continuity behavior:
- TUI now persists Codex resume session/thread IDs in the local session DB.
- Follow-up prompts in the same TUI session use `codex exec resume` automatically.
- If a saved Codex thread is no longer resumable, TUI falls back to a fresh Codex session and records a warning.

Default TUI model settings:
- codex backend model: `gpt-5.5`
- MTP providers: Each has a default model (e.g., `gpt-4o` for OpenAI, `llama-3.3-70b-versatile` for Groq)
- default reasoning effort: `medium` (Codex only)
- default autoresearch: `off` (MTP providers only)
- context window: detected from the selected model when known, otherwise a conservative provider fallback

Examples:

```bash
# Default backend is codex
mtp tui

# Start with Groq provider
mtp tui --backend groq

# Start with OpenRouter provider and custom model
mtp tui --backend openrouter

# Start with Claude provider
mtp tui --backend claude

# Set initial reasoning effort for codex backend
mtp tui --reasoning-effort high

# Enable autoresearch in MTP providers
mtp tui --backend groq --autoresearch --research-instructions "Verify completion before terminating."
```

Inside TUI:

**Backend & Model Management:**
- `/backend` - List all available providers with configuration status
- `/backend <provider>` - Switch to provider (codex, openai, groq, claude, gemini, openrouter, mistral, cohere, sambanova, cerebras, deepseek, togetherai, fireworksai)
- `/models` - Show all models for all providers
- `/model <name>` - Switch to model for current provider
- `/model add <provider> <name>` - Add custom model to any provider

**API Key Management:**
- `/apikey` - List all API keys (masked)
- `/apikey set <provider> <key>` - Set/update API key
- `/apikey delete <provider>` - Delete API key
- `/apikey show <provider>` - Show a masked API key

**Configuration:**
- `/reasoning <none|low|medium|high|xhigh>` - Set reasoning effort (Codex only)
- `/rounds <n>` - Set max_rounds (MTP providers)
- `/autoresearch on|off` - Toggle autoresearch (MTP providers)
- `/research <text>` - Set research instructions
- `/nerdfont <on|off>` - Toggle Nerd Font symbols (requires font support)
- `/nf <on|off>` - Shortcut for `/nerdfont`

**Session & Info:**
- `/status` - Show current session status
- `/codex login` - Run official Codex ChatGPT login flow
- `/codex logout` - Remove stored Codex credentials
- `/codex status` - Show Codex login status
- `/codex account` - Show Codex login email, profile metadata, config, and last captured usage/rate lines
- `/codex doctor` - Run Codex diagnostics
- `/codex repair-config` - Repair known Codex config issues
- `/codex-login` - Compatibility alias for `/codex login`
- `/exit` - Exit TUI

Model shortcuts (Codex only):
- `1 -> gpt-5.5`
- `2 -> gpt-5.4`
- `3 -> gpt-5.4-mini`
- `4 -> gpt-5.3-codex`

For MTP providers, use full model names or add custom models with `/model add <provider> <name>`.

Prompt UX:
- Use `@path/to/file.py` directly in your prompt to inject file context into the request.
- Example: `debug this @src/mtp/cli/tui.py and suggest a fix`

Usage visibility:
- After each response, TUI prints usage metrics including:
  - **Context bar**: Visual progress bar showing token usage against the selected model's known context limit
  - **Token breakdown**: `tokens(in/out/total/reasoning)=6316/796/7112/643`
  - **Cache metrics**: `cache(input/write/create/read)=1280/0/0/0` (when applicable)
  - **Performance**: `llm_calls=4`, `duration=10.80s`, `speed=658.5 tokens/s`
- `/status` includes the latest captured usage snapshot
- **Footer toolbar** shows:
  - **Codex**: `model · reasoning · backend · turns`
  - **MTP providers**: `model · autoresearch on/off · backend · turns`
- Context bar color-codes usage: 🟢 Green (0-60%), 🟡 Yellow (60-85%), 🔴 Red (85-100%)

Tool event streaming (MTP providers):
- Real-time tool execution visibility with `stream_tool_events=True`
- Shows tool name and reasoning: `🔧 file_read: Reading configuration file`
- Shows completion status: `✓ file_read completed` or `✗ file_read failed`
- Tool results are hidden by default (`stream_tool_results=False`) for cleaner output

### Aesthetics & Customization

**Nerd Font Mode**:
To enable high-fidelity icons, ensure you have a [Nerd Font](https://www.nerdfonts.com/) installed (e.g., FiraCode NF) and run:
`/nerdfont on`
*Note: Requires a CLI restart to apply all glyph changes.*

