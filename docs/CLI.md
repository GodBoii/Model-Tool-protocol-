# CLI and TUI

The `mtp` executable provides project scaffolding, diagnostics, provider
inspection, session transfer, codebase indexing, and an interactive terminal
UI. Run `mtp <command> --help` for the authoritative option list.

## Install and inspect

```bash
python -m pip install mtpx
mtp --version
mtp --help
```

The top-level commands are `new`, `run`, `doctor`, `providers`, `tui`,
`agent-os`, `sessions`, and `codebase`.

## Project commands

Create a scaffold:

```bash
mtp new my_agent
mtp new my_server --template mcp-http
mtp new my_memory_agent --template session-json
```

Options include `--template {minimal,mcp-http,session-json}`, `--dir`, and
`--force`. A scaffold contains starter Python code, `.env.example`, package
metadata, and a README.

Run a scaffold from its directory or an explicit path:

```bash
mtp run
mtp run --path ./my_agent
mtp run --path ./my_server --entry server.py
```

Without `--entry`, MTP checks `app.py`, `server.py`, then `main.py`.

## Diagnostics and provider inspection

```bash
mtp doctor
mtp doctor --provider groq
mtp doctor --provider openai --provider anthropic
mtp providers list
mtp providers list --json
mtp providers show groq
mtp providers show OpenAI --json
```

`doctor` checks the supported Python version, optional dotenv support, provider
SDK imports, and API-key readiness. Provider readiness recognizes both the
provider's environment variable and credentials saved by the TUI in the OS
credential vault. It never prints credential values and does not contact the
provider, so `ready` means locally configured, not remotely authenticated.

Warnings do not fail `doctor`; failed required checks do. Provider filtering is
repeatable. `providers show` accepts a registry name, public alias, or provider
class name.

## Session commands

The default session directory is `~/.mtp/sessions/`.

```bash
mtp sessions list
mtp sessions list --json --limit 20
mtp sessions show chat-123 --user-id alice --json
mtp sessions export backup.json --user-id alice
mtp sessions export one.json --session-id chat-123 --user-id alice
mtp sessions import backup.json --user-id alice
mtp sessions delete chat-123 --user-id alice --yes
```

`show` requires an owner when a session ID is ambiguous. Owner filters are
exact and import never rewrites ownership. Exports use the versioned envelope
defined by [`schemas/session-export-v1.schema.json`](schemas/session-export-v1.schema.json).
Export publication and import validation are atomic. Existing files or matching
`(session_id, user_id)` records are preserved unless `--force` is supplied.

## Codebase memory

```bash
mtp codebase memory
mtp codebase memory --on --path ./my_project
mtp codebase memory --off
mtp codebase status
```

When enabled, MTP stores an index at `<project>/.mtp/memory/codebase.sqlite`.
Generated, dependency, cache, VCS, and secret-like paths are skipped. The TUI
exposes the same controls as `/codebase memory [on|off|show]` and
`/codebase status`.

## Launch the TUI

```bash
mtp tui
mtp tui --backend groq
mtp tui --backend ollama
mtp tui --backend openrouter --mode review
```

`codex` is the default backend. It delegates to the installed official Codex
CLI and uses its login. MTP SDK backends are:

- Cloud: `openai`, `groq`, `claude`, `gemini`, `openrouter`, `mistral`,
  `cohere`, `sambanova`, `cerebras`, `deepseek`, `togetherai`, `fireworksai`,
  and `xiaomi`.
- Local: `ollama` and `lmstudio`.

Useful launch options include `--backend`, `--codex-model`, `--openai-model`,
`--max-rounds`, `--cwd`, `--session-db`, `--session-id`,
`--reasoning-effort`, `--mode`, `--autoresearch`, and
`--research-instructions`. Consult `mtp tui --help` for accepted values.

### Credentials

For a cloud backend, either set its standard environment variable or run:

```text
/apikey set groq
```

The TUI opens a masked entry dialog and stores the key in the operating
system's credential vault. Keys are excluded from command history,
autocomplete, transcripts, and editor undo/redo. MTP does not fall back to
plaintext if a usable keyring is unavailable. Legacy plaintext settings are
migrated only after the vault confirms the write.

Use `/apikey` to list configuration status, `/apikey show <provider>` to show a
masked value, and `/apikey delete <provider>` to remove a saved key.

### Slash commands

Press `Ctrl+P` for the searchable command palette or use these commands:

| Area | Commands |
| --- | --- |
| Help and state | `/help`, `/status`, `/history [n]`, `/tools`, `/details [toggle\|on\|off]` |
| Sessions | `/sessions`, `/new [label]`, `/load <id>`, `/open <id>` |
| Backend | `/backend <provider>`, `/models`, `/model <name>`, `/apikey ...` |
| Reasoning | `/reasoning <level>`, `/thinking <level>`, `/rounds <n>` |
| Harness | `/mode <plan\|code\|debug\|review>`, `/sandbox [mode]`, `/autoresearch on\|off`, `/research <text>` |
| Workspace | `/cd <dir>`, `/codebase memory [on\|off\|show]`, `/codebase status` |
| Codex | `/codex <login\|logout\|status\|account\|doctor\|repair-config>` |
| Display | `/clear`, `/compose`, `/exit` |

`/model <name>` accepts a custom model ID directly and persists it for the
current provider. There is no separate `/model add` command. Reasoning and
thinking choices are shown only when the current backend/model exposes them.

For Codex, `/sandbox` controls the Codex sandbox. For MTP SDK backends it selects
a tool permission profile. `read-only` blocks mutating harness tools;
`workspace-write` confines file edits and subprocess working directories to the
workspace and denies network-capable shell patterns; `danger-full-access`
removes those MTP harness restrictions. This is application-level enforcement,
not a general-purpose OS container.

### Prompt and streaming UX

Use `@relative/path.py` in a prompt to attach a workspace file. Attachment
reads, counts, and total bytes are bounded; paths outside the workspace and
secret-like files are rejected. The TUI renders provider text and tool events
incrementally, keeps bounded transcript/display state, and exposes usage data
when the provider returns it. Metrics vary by provider and model, so absent
cache, reasoning, or token fields are not synthesized.

Keyboard shortcuts include `Ctrl+P` for the command palette, `Ctrl+B` for the
sidebar, `Ctrl+L` to clear the display, and `Ctrl+Y` to copy the last response.

For local server setup and model discovery, see
[`TUI_LOCAL_INFERENCE.md`](TUI_LOCAL_INFERENCE.md).
