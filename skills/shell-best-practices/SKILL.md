---
name: shell-best-practices
description: Shell scripting best practices. Use when reading, editing, writing, or reviewing shell, Bash, PowerShell, CI scripts, or command-line automation.
---

# Shell best practices

Write scripts that are explicit, safe with paths, and predictable in CI and local terminals.

## Rules

- Prefer the platform's native shell for file operations. On Windows, use PowerShell cmdlets with `-LiteralPath` for paths.
- Quote variables and paths. Treat whitespace, glob characters, and empty strings as normal inputs.
- Fail loudly. In Bash, use `set -euo pipefail` when the script shape supports it. In PowerShell, set strict error behavior for automation.
- Check required commands and environment variables before doing work.
- Use temp directories safely and clean them up when appropriate.
- Do not build destructive commands from unchecked strings. Verify resolved paths before recursive delete or move operations.
- Keep scripts idempotent when they may run in CI, setup, deploy, or repair flows.
- Separate dry-run output from real mutation when risk is high.
- Prefer structured flags over positional argument puzzles for scripts people will reuse.
- Log what changed, not noisy progress. Include enough context to debug failures.
- Avoid secret leaks in command output, traces, process lists, and committed files.
