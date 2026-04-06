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

