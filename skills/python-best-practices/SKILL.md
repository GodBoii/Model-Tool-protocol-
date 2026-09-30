---
name: python-best-practices
description: Python best practices. Use when reading, editing, writing, or reviewing Python code.
---

# Python best practices

Write Python that is explicit, typed where useful, easy to test, and boring to operate.

## Rules

- Use type hints for public functions, important internal boundaries, and data structures that would otherwise be guessed.
- Prefer `dataclass`, `TypedDict`, `Protocol`, `Enum`, or Pydantic-style schemas when they make invalid data harder to pass around.
- Validate external input at the edge: CLI args, env vars, files, HTTP bodies, database rows, notebooks, and JSON.
- Keep business logic separate from I/O. Pure functions should not read files, call APIs, mutate globals, or depend on current time.
- Use `pathlib` for paths and context managers for files, locks, DB sessions, temp files, and network resources.
- Raise specific exceptions with useful context. Avoid broad `except Exception` unless you log, wrap, or re-raise.
- Do not swallow errors silently. If recovery is intentional, make that visible in code and logs.
- Avoid mutable default arguments. Use `None` plus explicit construction.
- Avoid module-level side effects beyond constants and cheap definitions. Do not start network calls or parse big files at import time.
- Prefer dependency injection for clients, clocks, random sources, and storage when tests need control.
- Write tests around behavior and edge cases. Use real local implementations where practical; mock only unavailable or expensive dependencies.
- Keep notebooks, scripts, and production modules separate. Shared logic belongs in importable modules, not copied cells.
- Use structured logging for shipped code. Avoid `print` except in CLI output or short throwaway scripts.
- Run the project formatter, linter, type checker, and tests when available.
