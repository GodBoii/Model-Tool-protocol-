# Codex Sandbox Mode Implementation

## Overview

This document describes the implementation of Codex CLI sandbox mode support in MTP TUI, which fixes the "workspace is read-only" issue.

## Problem Statement

Users reported that MTP TUI was unable to modify files because Codex CLI was running in read-only mode by default. The error message was:

```
I cannot update README.md because the workspace is read-only
```

## Root Cause

The Codex CLI uses a `--sandbox` flag to control file access permissions:
- **Default behavior**: `codex exec` runs in `read-only` mode (no file modifications)
- **Required for writes**: Must explicitly pass `--sandbox workspace-write`
- **Three modes available**:
  - `read-only`: Can only read files (safe mode)
  - `workspace-write`: Can modify files within workspace (normal development)
  - `danger-full-access`: Unrestricted file access (dangerous)

MTP TUI was not passing any sandbox flag, causing Codex to default to read-only mode.

## Solution

### 1. TUIState Enhancement

Changed from boolean `codex_write_mode` to string `codex_sandbox_mode`:

```python
@dataclass
class TUIState:
    # ... other fields ...
    codex_sandbox_mode: str  # "read-only", "workspace-write", or "danger-full-access"
```

**Default**: `"workspace-write"` (allows file modifications in workspace)

### 2. Codex Backend Integration

Updated `run_codex_prompt()` in `tui_codex_backend.py`:

```python
def run_codex_prompt(
    *,
    codex_bin: str,
    cwd: Path,
    prompt: str,
    model: str | None,
    reasoning_effort: str,
    previous_session_id: str | None,
    sandbox_mode: str = "workspace-write",  # NEW PARAMETER
    conversation_history: list[tuple[str, str]] | None = None,
    emit_live: Callable[[str, str], None] | None = None,
) -> CodexRunResult:
```

The command builder now includes:

```python
if sandbox_mode in ("read-only", "workspace-write", "danger-full-access"):
    cmd.extend(["--sandbox", sandbox_mode])
```

### 3. User Interface

#### New `/sandbox` Command

Replaces the old `/write` command with more granular control:

```bash
# Cycle through modes (no argument)
/sandbox

# Set specific mode
/sandbox read-only
/sandbox workspace-write
/sandbox danger-full-access

# Shortcuts
/sandbox readonly
/sandbox write
/sandbox full
```

#### Keyboard Shortcut (Ctrl+W)

Cycles through modes in order:
1. `read-only` → 
2. `workspace-write` → 
3. `danger-full-access` → 
4. (back to `read-only`)

Visual feedback with color-coded display:
- 🔒 **READ-ONLY** (yellow/warning)
- ✓ **WORKSPACE-WRITE** (green/success)
- ⚠ **DANGER-FULL-ACCESS** (red/error)

### 4. Session Persistence

Sandbox mode is saved in session metadata:

```json
{
  "tui": {
    "codex_sandbox_mode": "workspace-write",
    "backend": "codex",
    "codex_model": "gpt-5.3-codex",
    ...
  }
}
```

**Backward Compatibility**: Old sessions with `codex_write_mode: bool` are automatically migrated:
- `true` → `"workspace-write"`
- `false` → `"read-only"`

### 5. Status Display

The `/status` command now shows:

```
codex_sandbox_mode      WORKSPACE-WRITE ✓
```

With appropriate color coding based on the mode.

## Usage Examples

### Basic Usage

```bash
# Start TUI (defaults to workspace-write mode)
mtp tui

# Check current mode
/status

# Cycle through modes
/sandbox
# or press Ctrl+W

# Set specific mode
/sandbox read-only
```

### Safety Workflow

For sensitive operations, users can temporarily switch to read-only mode:

```bash
# Switch to safe mode
/sandbox read-only

# Review code without risk of modifications
"analyze the authentication code"

# Switch back to write mode when ready
/sandbox workspace-write

# Now make changes
"fix the authentication bug"
```

## Technical Details

### Command Construction

**Fresh Session**:
```bash
codex exec --skip-git-repo-check --cd <cwd> --sandbox workspace-write --json --output-last-message <file> <prompt>
```

**Resume Session**:
```bash
codex exec resume <session_id> --skip-git-repo-check --sandbox workspace-write --json --output-last-message <file> <prompt>
```

### Mode Descriptions

| Mode | Icon | Description | Use Case |
|------|------|-------------|----------|
| `read-only` | 🔒 | Can only read files | Code review, analysis, safe exploration |
| `workspace-write` | ✓ | Can modify files in workspace | Normal development, bug fixes, refactoring |
| `danger-full-access` | ⚠ | Unrestricted file access | System-level operations (use with caution) |

## Files Modified

1. **src/mtp/cli/tui.py**
   - Changed `TUIState.codex_write_mode: bool` → `codex_sandbox_mode: str`
   - Updated `_save_tui_session()` to persist new field
   - Updated `_load_session_into_state()` with backward compatibility
   - Updated `_run_codex_prompt()` to pass `sandbox_mode` parameter
   - Updated `_print_status()` to display sandbox mode with colors
   - Replaced `/write` command with `/sandbox` command
   - Updated help text and keyboard shortcuts
   - Updated `_normalize_input()` to include "sandbox"
   - Updated `run_tui()` initialization to default to `"workspace-write"`

2. **src/mtp/cli/tui_codex_backend.py**
   - Already had correct `sandbox_mode` parameter (from previous partial implementation)
   - `_build_codex_exec_command()` uses `--sandbox` flag
   - `run_codex_prompt()` accepts and passes through `sandbox_mode`

3. **src/mtp/cli/tui_completers.py**
   - Updated Ctrl+W handler to cycle through three modes
   - Added `/sandbox` to command autocomplete list
   - Updated visual feedback with color-coded notifications

## Testing

To verify the fix works:

```bash
# Start TUI
mtp tui

# Try to modify a file (should work now)
"update README.md to add a new section about installation"

# Verify the file was actually modified
# Check git status or file contents

# Test mode cycling
/sandbox
# Should cycle: workspace-write → danger-full-access → read-only → workspace-write

# Test read-only mode
/sandbox read-only
"update README.md"
# Should fail with read-only error

# Switch back to write mode
/sandbox workspace-write
"update README.md"
# Should succeed
```

## Migration Notes

**For existing users:**
- Old sessions with `codex_write_mode` will be automatically migrated
- Default mode is `workspace-write` (same behavior as old `write_mode=True`)
- The `/write` command is replaced by `/sandbox`
- Ctrl+W now cycles through three modes instead of toggling two

**For developers:**
- The `codex_sandbox_mode` field is required in TUIState
- Session metadata includes new `codex_sandbox_mode` key
- Codex backend functions now accept `sandbox_mode` parameter
- Valid values: `"read-only"`, `"workspace-write"`, `"danger-full-access"`

## References

- [Codex CLI Documentation](https://developers.openai.com/codex/cli)
- [Codex CLI Reference](https://developers.openai.com/codex/cli/reference)
- [Codex Sandbox Modes](https://developers.openai.com/codex/cli/features#sandbox-modes)
