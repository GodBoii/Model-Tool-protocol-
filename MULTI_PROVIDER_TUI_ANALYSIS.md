# Multi-Provider TUI Backend System - Comprehensive Analysis

## Executive Summary

This document provides a complete analysis of the MTP TUI CLI codebase and outlines the implementation strategy for adding multi-provider support beyond the current Codex-only backend.

---

## Current State Analysis

### 1. **Existing Backend Architecture**

#### Current Implementation (`src/mtp/cli/tui.py`)
- **Backends**: Only 2 backends currently defined:
  - `codex` - OpenAI's Codex CLI (external binary)
  - `mtp-openai` - MTP SDK with OpenAI provider (incomplete/non-functional)

```python
_BACKENDS = {"codex", "mtp-openai"}
```

#### Backend Command (`/backend`)
```python
if cmd == "/backend":
    if arg not in _BACKENDS:
        return f"{C_WARNING}Usage:{RESET} {C_CMD}/backend codex|mtp-openai{RESET}"
    state.backend = arg
    _save_tui_session(state)
    return f"{C_SUCCESS}{_SYM_OK}{RESET} Switched backend to {C_VALUE}{arg}{RESET}."
```

**Problem**: The `mtp-openai` backend doesn't work because:
1. No API key management
2. No model selection UI
3. Hardcoded to OpenAI only
4. No provider initialization logic

---

### 2. **Available MTP Providers**

MTP SDK already supports **13 providers** (`src/mtp/providers/__init__.py`):

| Provider | Alias | SDK Package | Status |
|----------|-------|-------------|--------|
| OpenAI | `OpenAI` | `openai` | ✅ Implemented |
| Groq | `Groq` | `groq` | ✅ Implemented |
| Anthropic (Claude) | `Anthropic` | `anthropic` | ✅ Implemented |
| Google Gemini | `Gemini` | `google-genai` | ✅ Implemented |
| OpenRouter | `OpenRouter` | `openai` (compatible) | ✅ Implemented |
| Mistral | `Mistral` | `mistralai` | ✅ Implemented |
| Cohere | `Cohere` | `cohere` | ✅ Implemented |
| SambaNova | `SambaNova` | `openai` (compatible) | ✅ Implemented |
| Cerebras | `Cerebras` | `openai` (compatible) | ✅ Implemented |
| DeepSeek | `DeepSeek` | `openai` (compatible) | ✅ Implemented |
| TogetherAI | `TogetherAI` | `openai` (compatible) | ✅ Implemented |
| FireworksAI | `FireworksAI` | `openai` (compatible) | ✅ Implemented |

---

### 3. **Provider Factory Pattern**

Already exists in `src/mtp/cli/tui_provider_factory.py`:

```python
SUPPORTED_TUI_PROVIDERS = (
    "openai", "groq", "claude", "openrouter", "gemini"
)

PROVIDER_BUILDERS = {
    "openai": _openai_builder,
    "groq": _groq_builder,
    "claude": _claude_builder,
    "openrouter": _openrouter_builder,
    "gemini": _gemini_builder,
}
```

**Current Limitation**: Only 5 providers supported, missing 8 others.

---

### 4. **Provider Settings Storage**

Already exists in `src/mtp/cli/tui_settings.py`:

```python
DEFAULT_PROVIDER_MODELS = {
    "openai": "gpt-5.4-mini",
    "groq": "llama-3.3-70b-versatile",
    "claude": "claude-3-5-sonnet-20241022",
    "openrouter": "qwen/qwen3.6-plus-preview:free",
    "gemini": "gemini-2.0-flash",
}
```

**Storage Location**: `{session_db_path}/tui_provider_settings.json`

**Schema**:
```json
{
  "providers": {
    "openai": {
      "api_key": "sk-...",
      "model": "gpt-4o",
      "models": ["gpt-4o", "gpt-4o-mini", "custom-model"]
    }
  }
}
```

---

### 5. **Codex Backend Integration**

#### Codex-Specific Features (`src/mtp/cli/tui_codex_backend.py`)
- **Binary Detection**: `detect_codex_bin()` - finds `codex.cmd` or `codex` in PATH
- **Login Flow**: `run_codex_login()` - runs `codex login` for authentication
- **Session Management**: Thread/session ID tracking for conversation continuity
- **Sandbox Modes**: 
  - `read-only` - Safe mode, no file writes
  - `workspace-write` - Can modify files in workspace
  - `danger-full-access` - Unrestricted access
- **Reasoning Effort**: `none`, `low`, `medium`, `high`, `xhigh`
- **Model Support**: `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`, etc.

#### Codex Command Construction
```python
def _build_codex_exec_command(
    codex_bin, cwd, output_path, prompt, model,
    reasoning_effort, session_id, sandbox_mode
):
    if session_id:
        # Resume: NO -C flag
        cmd = [codex_bin, "exec", "resume", session_id, ...]
    else:
        # Fresh: use --cd
        cmd = [codex_bin, "exec", "--cd", str(cwd), ...]
```

---

## Implementation Strategy

### Phase 1: Expand Provider Support

#### 1.1 Update Provider Factory
**File**: `src/mtp/cli/tui_provider_factory.py`

Add all 13 providers:

```python
SUPPORTED_TUI_PROVIDERS = (
    "codex",      # Keep for backward compatibility
    "openai",
    "groq",
    "claude",
    "gemini",
    "openrouter",
    "mistral",
    "cohere",
    "sambanova",
    "cerebras",
    "deepseek",
    "togetherai",
    "fireworksai",
)

DEFAULT_PROVIDER_MODELS = {
    "codex": "gpt-5.3-codex",
    "openai": "gpt-4o",
    "groq": "llama-3.3-70b-versatile",
    "claude": "claude-3-5-sonnet-20241022",
    "gemini": "gemini-2.0-flash",
    "openrouter": "qwen/qwen3.6-plus-preview:free",
    "mistral": "mistral-large-latest",
    "cohere": "command-r-plus",
    "sambanova": "Meta-Llama-3.1-405B-Instruct",
    "cerebras": "llama3.1-70b",
    "deepseek": "deepseek-chat",
    "togetherai": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
    "fireworksai": "accounts/fireworks/models/llama-v3p1-70b-instruct",
}
```

#### 1.2 Add Missing Provider Builders

```python
def _mistral_builder(model: str, api_key: str | None) -> Any:
    return Mistral(model=model, api_key=api_key)

def _cohere_builder(model: str, api_key: str | None) -> Any:
    return Cohere(model=model, api_key=api_key)

# ... add remaining 6 providers
```

---

### Phase 2: Redesign `/backend` Command

#### 2.1 New Command Structure

**Current**:
```
/backend codex|mtp-openai
```

**New**:
```
/backend                    # List all providers with status
/backend <provider>         # Switch to provider (with setup flow)
```

#### 2.2 Provider Listing UI

```python
def _print_provider_list(state: TUIState, settings: dict) -> None:
    """
    Display all available providers with setup status.
    
    Example output:
    ┌─ Available Providers ─────────────────────────────────────┐
    │                                                            │
    │  ● codex          gpt-5.3-codex         ✓ Ready           │
    │  ○ openai         (not configured)      ⚠ Setup needed    │
    │  ● groq           llama-3.3-70b         ✓ Ready           │
    │  ○ claude         (not configured)      ⚠ Setup needed    │
    │  ● gemini         gemini-2.0-flash      ✓ Ready           │
    │                                                            │
    │  Usage: /backend <provider>                                │
    └────────────────────────────────────────────────────────────┘
    """
```

#### 2.3 Provider Setup Flow

When user runs `/backend <provider>` for an unconfigured provider:

```python
def _setup_provider_interactive(state: TUIState, provider_name: str) -> str | None:
    """
    Interactive setup for a new provider.
    
    Steps:
    1. Check if API key exists in settings
    2. If not, prompt for API key
    3. Validate API key (optional test call)
    4. Prompt for model name (with default suggestion)
    5. Save settings
    6. Switch to provider
    """
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    
    entry = ensure_provider_entry(settings, provider_name)
    
    # Step 1: API Key
    if not entry.get("api_key"):
        print(f"\n  {C_LABEL}Setup {provider_name}{RESET}")
        print(f"  {C_DIM}API key not found. Please provide your API key.{RESET}")
        api_key = input(f"  {C_PROMPT_ARROW}API Key:{RESET} ").strip()
        
        if not api_key:
            return f"{C_ERROR}Setup cancelled.{RESET}"
        
        entry["api_key"] = api_key
    
    # Step 2: Model Selection
    default_model = DEFAULT_PROVIDER_MODELS.get(provider_name)
    print(f"\n  {C_LABEL}Model Selection{RESET}")
    print(f"  {C_DIM}Default: {default_model}{RESET}")
    model = input(f"  {C_PROMPT_ARROW}Model (press Enter for default):{RESET} ").strip()
    
    if not model:
        model = default_model
    
    entry["model"] = model
    
    # Step 3: Save
    save_provider_settings(settings_path, settings)
    
    return f"{C_SUCCESS}✓{RESET} Provider {C_VALUE}{provider_name}{RESET} configured successfully!"
```

---

### Phase 3: Add `/model add` Command

#### 3.1 Command Implementation

```python
if cmd == "/model":
    if not arg:
        _print_model_matrix(state)
        return f"{C_WARNING}Usage:{RESET} {C_CMD}/model <name|add <model>>{RESET}"
    
    # Handle "/model add <model-name>"
    if arg.startswith("add "):
        model_name = arg[4:].strip()
        if not model_name:
            return f"{C_ERROR}Model name required.{RESET}"
        
        # Add to provider's model list
        settings_path = provider_settings_path(state.session_store.file_path)
        settings = load_provider_settings(settings_path)
        
        provider_name = state.backend
        if provider_name == "codex":
            return f"{C_WARNING}Cannot add custom models to Codex backend.{RESET}"
        
        entry = ensure_provider_entry(settings, provider_name)
        models = entry.get("models", [])
        
        if model_name in models:
            return f"{C_WARNING}Model {C_VALUE}{model_name}{RESET} already exists.{RESET}"
        
        models.append(model_name)
        entry["models"] = models
        save_provider_settings(settings_path, settings)
        
        return f"{C_SUCCESS}✓{RESET} Added model {C_VALUE}{model_name}{RESET} to {provider_name}."
    
    # Handle "/model <name>" (switch model)
    resolved = _resolve_model(arg)
    # ... existing logic
```

#### 3.2 Model Listing Enhancement

Update `_print_model_matrix()` to show custom models:

```python
def _print_model_matrix(state: TUIState) -> None:
    """
    Show available models for current provider.
    
    For Codex: Show preset models
    For MTP providers: Show default + custom models
    """
    if state.backend == "codex":
        # Existing Codex model matrix
        _print_codex_model_matrix(state)
    else:
        # New MTP provider model matrix
        _print_mtp_provider_model_matrix(state)

def _print_mtp_provider_model_matrix(state: TUIState) -> None:
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    entry = ensure_provider_entry(settings, state.backend)
    
    default_model = DEFAULT_PROVIDER_MODELS.get(state.backend)
    custom_models = entry.get("models", [])
    current_model = entry.get("model", default_model)
    
    print(f"\n  {C_BRAND_BOLD}Models for {state.backend}{RESET}")
    print(f"  {C_LABEL}Default:{RESET} {C_MODEL}{default_model}{RESET}")
    
    if custom_models:
        print(f"\n  {C_LABEL}Custom Models:{RESET}")
        for model in custom_models:
            marker = f"{C_SUCCESS}●{RESET}" if model == current_model else f"{C_DIM}○{RESET}"
            print(f"    {marker} {C_MODEL}{model}{RESET}")
    
    print(f"\n  {C_DIM}Usage:{RESET}")
    print(f"    {C_CMD}/model <name>{RESET}        {C_DIM}Switch to model{RESET}")
    print(f"    {C_CMD}/model add <name>{RESET}    {C_DIM}Add custom model{RESET}")
```

---

### Phase 4: Update TUI State Management

#### 4.1 Expand TUIState Dataclass

```python
@dataclass
class TUIState:
    backend: str                    # Current provider name
    codex_model: str | None         # Codex-specific model
    openai_model: str               # Legacy, keep for backward compat
    
    # NEW: Unified provider state
    active_provider_model: str | None  # Current model for active provider
    provider_api_keys: dict[str, str]  # Cached API keys per provider
    
    max_rounds: int
    cwd: Path
    autoresearch: bool
    research_instructions: str | None
    reasoning_effort: str
    codex_sandbox_mode: str
    
    # ... rest of fields
```

#### 4.2 Session Persistence

Update `_save_tui_session()` and `_load_session_into_state()` to handle new fields:

```python
def _save_tui_session(state: TUIState) -> None:
    metadata["tui"] = {
        "backend": state.backend,
        "codex_model": state.codex_model,
        "active_provider_model": state.active_provider_model,
        # ... other fields
    }
```

---

### Phase 5: Implement Provider Switching Logic

#### 5.1 Backend Switch Handler

```python
def _switch_backend(state: TUIState, provider_name: str) -> str:
    """
    Switch to a different provider backend.
    
    Flow:
    1. Check if provider is configured
    2. If not, run interactive setup
    3. Load provider settings
    4. Initialize MTP agent with provider
    5. Update state
    6. Save session
    """
    # Normalize provider name
    provider_name = provider_name.lower().strip()
    
    # Special case: Codex
    if provider_name == "codex":
        if not state.codex_bin:
            codex_bin = _detect_codex_bin()
            if not codex_bin:
                return f"{C_ERROR}Codex CLI not found. Install from OpenAI.{RESET}"
            state.codex_bin = codex_bin
        
        state.backend = "codex"
        state.agent = None  # Clear MTP agent
        _save_tui_session(state)
        return f"{C_SUCCESS}✓{RESET} Switched to Codex backend."
    
    # MTP Provider
    if provider_name not in SUPPORTED_TUI_PROVIDERS:
        return f"{C_ERROR}Unknown provider: {provider_name}{RESET}"
    
    # Check configuration
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    entry = ensure_provider_entry(settings, provider_name)
    
    # Setup if needed
    if not entry.get("api_key") or not entry.get("model"):
        setup_result = _setup_provider_interactive(state, provider_name)
        if setup_result and "cancelled" in setup_result.lower():
            return setup_result
        # Reload settings after setup
        settings = load_provider_settings(settings_path)
        entry = ensure_provider_entry(settings, provider_name)
    
    # Build provider
    try:
        selection = ProviderSelection(
            provider_name=provider_name,
            model_name=entry["model"],
            api_key=entry.get("api_key"),
        )
        provider = build_tui_provider(selection)
    except Exception as e:
        return f"{C_ERROR}Failed to initialize provider: {e}{RESET}"
    
    # Initialize agent
    state.backend = provider_name
    state.active_provider_model = entry["model"]
    state.agent = Agent.MTPAgent(
        provider=provider,
        tools=state.tools,  # Reuse existing tool registry
        instructions=state.instructions,
        debug_mode=state.debug_mode,
        strict_dependency_mode=state.strict_dependency_mode,
        autoresearch=state.autoresearch,
        research_instructions=state.research_instructions,
        session_store=state.session_store,
    )
    
    _save_tui_session(state)
    
    return (
        f"{C_SUCCESS}✓{RESET} Switched to {C_VALUE}{provider_name}{RESET} "
        f"with model {C_MODEL}{entry['model']}{RESET}."
    )
```

---

### Phase 6: Update Chat Execution Logic

#### 6.1 Unified Chat Handler

```python
def _execute_chat(state: TUIState, prompt: str, attachments: list[str]) -> ChatResult:
    """
    Execute chat with current backend (Codex or MTP provider).
    """
    if state.backend == "codex":
        return _execute_codex_chat(state, prompt, attachments)
    else:
        return _execute_mtp_chat(state, prompt, attachments)

def _execute_mtp_chat(state: TUIState, prompt: str, attachments: list[str]) -> ChatResult:
    """
    Execute chat using MTP SDK provider.
    """
    if state.agent is None:
        # Lazy initialization
        switch_result = _switch_backend(state, state.backend)
        if "Failed" in switch_result or "cancelled" in switch_result:
            return ChatResult(
                text=switch_result,
                tool_events=[],
                attachments=[],
                warnings=[],
                usage_lines=[],
            )
    
    try:
        # Run agent
        result = state.agent.run_loop(
            user_input=prompt,
            max_rounds=state.max_rounds,
            stream=False,  # TODO: Add streaming support
        )
        
        # Extract tool events from agent state
        tool_events = []  # TODO: Capture from agent events
        
        return ChatResult(
            text=result,
            tool_events=tool_events,
            attachments=attachments,
            warnings=[],
            usage_lines=[],  # TODO: Extract from agent metadata
        )
    except Exception as e:
        return ChatResult(
            text=f"Error: {e}",
            tool_events=[],
            attachments=attachments,
            warnings=[str(e)],
            usage_lines=[],
        )
```

---

## File Changes Summary

### Files to Modify

1. **`src/mtp/cli/tui_provider_factory.py`**
   - Add 8 missing providers
   - Update `SUPPORTED_TUI_PROVIDERS`
   - Add provider builders

2. **`src/mtp/cli/tui_settings.py`**
   - Add default models for new providers
   - Add helper functions for model management

3. **`src/mtp/cli/tui.py`**
   - Update `_BACKENDS` constant
   - Rewrite `/backend` command handler
   - Add `/model add` command
   - Update `_print_model_matrix()`
   - Add `_print_provider_list()`
   - Add `_setup_provider_interactive()`
   - Add `_switch_backend()`
   - Update `_execute_chat()` to route to correct backend
   - Update `TUIState` dataclass

4. **`src/mtp/cli/tui_completers.py`**
   - Update command completions for new `/backend` syntax
   - Add completions for provider names

### New Files to Create

1. **`src/mtp/cli/tui_mtp_backend.py`**
   - MTP provider execution logic
   - Event streaming for MTP providers
   - Usage metrics extraction
   - Tool event formatting

---

## Testing Strategy

### Test Cases

1. **Provider Switching**
   - Switch from Codex to OpenAI
   - Switch from OpenAI to Groq
   - Switch back to Codex
   - Verify session persistence

2. **Provider Setup**
   - Setup new provider (API key + model)
   - Cancel setup flow
   - Re-setup existing provider

3. **Model Management**
   - Add custom model
   - Switch to custom model
   - List models for provider

4. **Chat Execution**
   - Send message with Codex backend
   - Send message with MTP provider backend
   - Verify tool calls work
   - Verify attachments work

5. **Session Persistence**
   - Save session with provider settings
   - Load session and verify provider restored
   - Switch providers mid-session

---

## Migration Path

### Backward Compatibility

1. **Existing Sessions**
   - Old sessions with `backend: "codex"` continue to work
   - Old sessions with `backend: "mtp-openai"` migrate to `backend: "openai"`

2. **Command Aliases**
   - `/backend codex` still works
   - `/backend mtp-openai` redirects to `/backend openai` with deprecation warning

3. **Settings Migration**
   - Auto-migrate old `openai_model` field to new provider settings format

---

## Implementation Checklist

### Phase 1: Foundation
- [ ] Update `tui_provider_factory.py` with all 13 providers
- [ ] Update `tui_settings.py` with default models
- [ ] Add provider builder functions

### Phase 2: Commands
- [ ] Rewrite `/backend` command
- [ ] Add `/backend` (list) functionality
- [ ] Add `/model add` command
- [ ] Update `/model` command for provider-specific models

### Phase 3: UI
- [ ] Create `_print_provider_list()` function
- [ ] Update `_print_model_matrix()` for MTP providers
- [ ] Add provider setup UI (`_setup_provider_interactive()`)

### Phase 4: Backend Logic
- [ ] Create `tui_mtp_backend.py` module
- [ ] Implement `_switch_backend()` function
- [ ] Implement `_execute_mtp_chat()` function
- [ ] Add MTP event streaming support

### Phase 5: State Management
- [ ] Update `TUIState` dataclass
- [ ] Update `_save_tui_session()`
- [ ] Update `_load_session_into_state()`
- [ ] Add migration logic for old sessions

### Phase 6: Testing
- [ ] Test all 13 providers
- [ ] Test provider switching
- [ ] Test model management
- [ ] Test session persistence
- [ ] Test backward compatibility

---

## Estimated Effort

- **Phase 1**: 2-3 hours
- **Phase 2**: 3-4 hours
- **Phase 3**: 2-3 hours
- **Phase 4**: 4-5 hours
- **Phase 5**: 2-3 hours
- **Phase 6**: 3-4 hours

**Total**: 16-22 hours

---

## Conclusion

The MTP TUI CLI has a solid foundation with provider factory patterns and settings storage already in place. The main work involves:

1. Expanding provider support from 5 to 13
2. Redesigning the `/backend` command for better UX
3. Adding `/model add` for custom model management
4. Implementing provider switching logic
5. Creating MTP backend execution module
6. Ensuring backward compatibility

The architecture is well-designed and modular, making this expansion straightforward. The key insight is that Codex remains a special case (external binary), while all other providers use the unified MTP SDK pattern.
