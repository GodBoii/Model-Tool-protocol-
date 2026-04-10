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

Returns non-zero if warnings are detected.

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

## `mtp tui`

Launch the interactive terminal UI.

```bash
mtp tui
```

Recommended launch command:
- `mtp tui` (single top-level command, consistent with existing CLI)

Backends:
- `codex`: bridges to official Codex CLI (`codex exec`) and uses your Codex login session.
- `mtp-openai`: uses MTP SDK with `OpenAI` provider and local toolkits.

Default TUI model settings:
- codex backend model: `gpt-5.3-codex`
- mtp-openai backend model: `gpt-5.4-mini`
- default reasoning effort: `medium`

Examples:

```bash
# Default backend is codex
mtp tui

# Start directly in MTP OpenAI backend
mtp tui --backend mtp-openai --openai-model gpt-5.4-mini

# Set initial reasoning effort for codex backend
mtp tui --reasoning-effort high

# Enable autoresearch in MTP OpenAI backend
mtp tui --backend mtp-openai --autoresearch --research-instructions "Verify completion before terminating."
```

Inside TUI:
- `/models`
- `/backend codex|mtp-openai`
- `/model <name|1..4|default>`
- `/reasoning <none|low|medium|high|xhigh>`
- `/rounds <n>`
- `/codex-login`
- `/autoresearch on|off`
- `/research <text>`
- `/status`
- `/exit`

Model shortcuts:
- `1 -> gpt-5.4`
- `2 -> gpt-5.4-mini`
- `3 -> gpt-5.3-codex`
- `4 -> gpt-5.2`

Prompt UX:
- Use `@path/to/file.py` directly in your prompt to inject file context into the request.
- Example: `debug this @src/mtp/cli/tui.py and suggest a fix`

Usage visibility:
- After each response, TUI prints a `Usage` block with token totals and context-window usage (when model window is known).
- `/status` includes the latest captured usage snapshot.
- Rate-limit remaining is shown as best-effort for codex backend and header-backed for `mtp-openai` when OpenAI rate-limit headers are returned.
