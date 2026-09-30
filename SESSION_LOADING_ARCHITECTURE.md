# Session Loading Architecture - Deep Analysis & Implementation

## Problem Statement

The MTP TUI failed to load previous sessions with error "Session not found" because it only checked the local `JsonSessionStore` database. Users expected to be able to load:

1. **Codex CLI threads** created outside MTP TUI
2. **MTP sessions** saved in the local database
3. **Partial session IDs** (fuzzy matching)

## Session Management Updates (April 2026)

### New Features

**Auto-Generated Session Titles:**
- Session titles are now automatically generated from the first user message
- Extracts first 3-4 meaningful words (e.g., "How do I implement" from "How do I implement authentication")
- Removes file attachments (@file syntax) from titles
- Fallback to "Quick chat" for very short prompts
- Manual labels via `/new [label]` still supported

**Centralized Session Storage:**
- Changed from: `tmp/mtp_tui_sessions/` (per-directory)
- Changed to: `~/.mtp/sessions/` (centralized)
- All sessions from all projects stored in one location
- Sessions accessible from any directory
- Sessions persist even if project directories are deleted

**Directory-Based Grouping:**
- Sessions grouped by working directory when listed
- Current directory sessions shown first (marked with ●)
- Other directories shown below (marked with ○)
- Easy to see which sessions belong to which project

## Root Cause Analysis

### Dual Session Management System

MTP operates with TWO separate session systems:

```
┌─────────────────────────────────────────────────────────────┐
│                    MTP TUI Session                          │
│  ID: chat-{uuid}                                            │
│  Storage: ~/.mtp/sessions/mtp_sessions.json                │
│  Contains:                                                   │
│    - Transcript (user/assistant messages)                   │
│    - Settings (model, reasoning, backend)                   │
│    - Metadata (labels, timestamps, cwd)                     │
│    - Reference to Codex thread ID                           │
│    - Auto-generated session title                           │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ references
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  Codex CLI Thread                           │
│  ID: thread-{uuid}                                          │
│  Storage: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl     │
│  Contains:                                                   │
│    - Full conversation state                                │
│    - Tool call history                                      │
│    - File modifications                                     │
│    - Reasoning chains                                       │
└─────────────────────────────────────────────────────────────┘
```

### Why Codex Resume is Critical

**Without `codex exec resume <thread_id>`:**
- Every follow-up query starts a NEW conversation
- Codex loses ALL context:
  - Previous file edits
  - Tool calls made
  - Reasoning chains
  - User preferences
- Manual history injection is:
  - Token-expensive (uses context window)
  - Lossy (only text, no tool state)
  - Limited (only last 5 turns)

**With `codex exec resume <thread_id>`:**
- Codex maintains full internal state
- Zero token overhead for history
- Complete context preservation
- Efficient continuation

### The Original Bug

```python
# OLD CODE - Only checked local database
if cmd == "/load":
    record = _load_session_record(state, arg)  # ← Only JsonSessionStore
    if record is None:
        return "Session not found"  # ← No fallback!
```

**Failure scenarios:**
1. User provides Codex thread ID → Not in JsonStore → Fails
2. User provides partial ID → Exact match fails → Fails
3. Session never saved → Not in JsonStore → Fails
4. Database corrupted → Parse fails → Fails

## Solution: Hierarchical Session Resolution

### Architecture

```
User Input: "49f7hd7"
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 1: Try as Codex Thread ID                             │
│                                                              │
│ • Check: thread-49f7hd7, 49f7hd7                           │
│ • Search: ~/.codex/sessions/**/rollout-*.jsonl             │
│ • If found → Import as MTP session                         │
│ • If already imported → Load existing MTP session          │
└─────────────────────────────────────────────────────────────┘
    │ Not found
    ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 2: Try as MTP Session ID (Exact Match)               │
│                                                              │
│ • Lookup: JsonSessionStore.get_session("49f7hd7")         │
│ • If found → Load session                                   │
└─────────────────────────────────────────────────────────────┘
    │ Not found
    ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 3: Try Fuzzy Match on MTP Sessions                   │
│                                                              │
│ • Match: session_id.endswith("49f7hd7")                    │
│ • Match: "49f7hd7" in session_id                           │
│ • Match: Last part after dash contains "49f7hd7"          │
│ • If found → Load session                                   │
└─────────────────────────────────────────────────────────────┘
    │ Not found
    ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 4: Show Helpful Error with Suggestions               │
│                                                              │
│ • List recent 5 sessions                                    │
│ • Show session IDs, labels, turn counts                    │
│ • Suggest /sessions command                                │
└─────────────────────────────────────────────────────────────┘
```

### Implementation Details

#### 1. Codex Session File Discovery

```python
def _find_codex_session_file(thread_id: str) -> Path | None:
    """
    Search Codex's local session storage.
    
    Location: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
    """
    codex_home = Path.home() / ".codex" / "sessions"
    
    # Search last 30 days
    for days_ago in range(30):
        date = datetime.now() - timedelta(days=days_ago)
        date_path = codex_home / date.strftime("%Y/%m/%d")
        
        for session_file in date_path.glob("rollout-*.jsonl"):
            # Check filename
            if thread_id in session_file.name:
                return session_file
            
            # Check file content (first 20 lines)
            with session_file.open("r") as f:
                for i, line in enumerate(f):
                    if i > 20:
                        break
                    if thread_id in line:
                        return session_file
    
    return None
```

**Why this works:**
- Codex CLI stores ALL sessions locally as JSONL files
- Thread IDs appear in filename or early in file
- Searching 30 days covers most use cases
- Fast: Only checks first 20 lines per file

#### 2. Codex Thread Validation

```python
def _try_codex_thread_resume(state: TUIState, thread_id: str) -> tuple[bool, str | None]:
    """
    Check if Codex thread is valid and resumable.
    """
    # Check if session file exists
    session_file = _find_codex_session_file(thread_id)
    if session_file is None:
        return False, "Thread not found in Codex session storage"
    
    # File exists → thread is valid
    return True, None
```

**Why not test actual resume:**
- `codex exec resume` has no `--dry-run` flag
- Testing would execute the thread (side effects)
- File existence is sufficient validation
- Actual resume happens on first use

#### 3. Importing Codex Threads as MTP Sessions

```python
def _create_mtp_session_from_codex_thread(state: TUIState, thread_id: str) -> SessionRecord | None:
    """
    Parse Codex JSONL file and create MTP session wrapper.
    """
    session_file = _find_codex_session_file(thread_id)
    
    # Parse JSONL to extract conversation
    transcript = []
    with session_file.open("r") as f:
        for line in f:
            event = json.loads(line)
            
            # Extract user/assistant messages
            if "user" in event.get("type", ""):
                user_msg = event.get("content")
            if "assistant" in event.get("type", ""):
                assistant_msg = event.get("content")
            
            # Create transcript turn
            if user_msg and assistant_msg:
                transcript.append(TranscriptTurn(...))
    
    # Create MTP session record
    record = SessionRecord(
        session_id=_new_session_id(),
        metadata={
            "tui": {
                "codex_session_id": thread_id,  # ← Link to Codex thread
                "transcript": transcript,
                "imported_from_codex": True,
            }
        }
    )
    
    # Save to JsonStore
    state.session_store.upsert_session(record)
    
    return record
```

**Benefits:**
- Codex threads become first-class MTP sessions
- Full conversation history preserved
- Can resume Codex thread on next query
- Unified session management

#### 4. Fuzzy Matching

```python
def _find_session_by_partial_id(store: JsonSessionStore, partial_id: str) -> SessionRecord | None:
    """
    Find session by partial ID.
    """
    sessions = _list_saved_sessions(store)
    
    # Try exact suffix match
    for session in sessions:
        if session.session_id.endswith(partial_id):
            return session
    
    # Try contains match
    for session in sessions:
        if partial_id in session.session_id:
            return session
    
    # Try last part after dash
    for session in sessions:
        parts = session.session_id.split("-")
        if partial_id in parts[-1]:
            return session
    
    return None
```

**Examples:**
- Input: `49f7hd7` → Matches: `chat-49f7hd7abc`
- Input: `abc` → Matches: `chat-abc123def`
- Input: `123` → Matches: `chat-xyz-123`

#### 5. Helpful Error Messages

```python
if not sessions:
    return (
        "No sessions found.\n"
        "Session database: tmp/mtp_tui_sessions/\n"
        "Codex sessions: ~/.codex/sessions/\n"
        "Try: /sessions to list available"
    )

# Show recent sessions
recent = sessions[:5]
suggestions = [
    f"{sid_short} - {label} ({turn_count} turns)"
    for s in recent
]

return (
    f"Session not found: {session_id_input}\n\n"
    f"Recent sessions:\n" +
    "\n".join(suggestions) +
    "\n\nTry: /sessions to see all"
)
```

**User experience:**
- Clear error message
- Shows what was searched
- Lists recent sessions as suggestions
- Provides next steps

## Conversation History Preservation

### How Resume Works

```python
# First query (fresh session)
$ codex exec --cd /project "help me debug"
# Codex creates thread-abc123
# Returns response

# Follow-up query (resume)
$ codex exec resume thread-abc123 "what about the other file?"
# Codex loads thread-abc123 state
# Has FULL context from previous query
# Returns response with context
```

### MTP Integration

```python
def _run_codex_prompt(state: TUIState, prompt: str) -> ChatResult:
    # Build conversation history from MTP transcript
    conversation_history = [
        (turn.prompt, turn.response)
        for turn in state.transcript
    ]
    
    # Run Codex with resume
    result = codex_backend.run_codex_prompt(
        codex_bin=codex_bin,
        cwd=state.cwd,
        prompt=prompt,
        previous_session_id=state.codex_session_id,  # ← Resume thread
        conversation_history=conversation_history,    # ← Fallback
    )
    
    # Update Codex thread ID
    state.codex_session_id = result.session_id
    
    return result
```

### Fallback: Manual History Injection

When Codex thread expires (after ~24-48 hours):

```python
def _build_prompt_with_history(
    current_prompt: str,
    conversation_history: list[tuple[str, str]],
    max_turns: int = 5,
) -> str:
    """
    Inject conversation history into prompt.
    
    Used when Codex thread is expired/invalid.
    """
    recent = conversation_history[-max_turns:]
    
    history_parts = []
    for user_msg, assistant_msg in recent:
        # Truncate to avoid token limits
        user_truncated = user_msg[:500] + "..." if len(user_msg) > 500 else user_msg
        assistant_truncated = assistant_msg[:800] + "..." if len(assistant_msg) > 800 else assistant_msg
        
        history_parts.append(f"User: {user_truncated}")
        history_parts.append(f"Assistant: {assistant_truncated}")
        history_parts.append("")
    
    history_parts.append(f"User: {current_prompt}")
    
    return "\n".join(history_parts)
```

**When used:**
```python
if return_code != 0 and previous_session_id:
    # Resume failed → Retry with history injection
    retry_prompt = _build_prompt_with_history(prompt, conversation_history)
    
    fresh_cmd = _build_codex_exec_command(
        session_id=None,  # ← Fresh session
        prompt=retry_prompt,  # ← With history
    )
```

**Trade-offs:**
- ✅ Works when thread expires
- ✅ Better than no context
- ❌ Token-expensive (uses context window)
- ❌ Only last 5 turns
- ❌ Loses tool call state
- ❌ Loses file edit history

## Usage Examples

### Example 1: Load Codex Thread

```bash
# User ran Codex CLI directly
$ codex "help me refactor this code"
# Codex creates thread-xyz789

# Later, in MTP TUI
$ mtp tui
> /load xyz789

# Output:
→ Checking if 'xyz789' is a Codex thread...
✓ Found Codex thread: thread-xyz789
→ Creating MTP session wrapper for Codex thread...
✓ Imported Codex thread thread-xyz789... as MTP session chat-abc123 with 3 turns.

# Now can continue conversation
> "what about the other file?"
# Codex resumes thread-xyz789 with full context
```

### Example 2: Load MTP Session

```bash
$ mtp tui
> "help me debug"
# Creates chat-abc456, Codex creates thread-xyz789

> /exit

$ mtp tui
> /load abc456

# Output:
→ Checking if 'abc456' is a Codex thread...
✗ Not a valid Codex thread
→ Checking MTP sessions (exact match)...
✗ No exact match
→ Trying fuzzy match on MTP sessions...
✓ Found MTP session (fuzzy match): chat-abc456def
✓ Loaded session chat-abc456def (matched 'abc456') with 5 turns.

# Conversation continues with Codex thread-xyz789
> "what about the tests?"
# Codex resumes thread-xyz789
```

### Example 3: Session Not Found

```bash
$ mtp tui
> /load invalid123

# Output:
→ Checking if 'invalid123' is a Codex thread...
✗ Not a valid Codex thread
→ Checking MTP sessions (exact match)...
✗ No exact match
→ Trying fuzzy match on MTP sessions...
✗ No fuzzy match
✗ Session not found: invalid123

Recent sessions:
  49f7hd7 - Debug session (5 turns)
  abc123d - Refactor work (3 turns)
  xyz789a - Code review (8 turns)

Try: /sessions to see all sessions
```

## Benefits of This Architecture

### 1. Unified Session Management
- Codex threads and MTP sessions work together
- No confusion about which ID to use
- Automatic import of external Codex threads

### 2. Conversation Continuity
- `codex exec resume` preserves full context
- Manual history injection as fallback
- No loss of conversation flow

### 3. User-Friendly
- Fuzzy matching for partial IDs
- Helpful error messages with suggestions
- Transparent import process

### 4. Robust
- Multiple fallback strategies
- Handles expired threads gracefully
- Works with sessions created outside MTP

### 5. Efficient
- Zero token overhead for resume
- Fast session file discovery
- Minimal parsing of JSONL files

## Future Enhancements

### 1. Session Sync
- Periodically scan ~/.codex/sessions/
- Auto-import new Codex threads
- Keep MTP database in sync

### 2. Session Metadata
- Extract more info from Codex JSONL
- Show file changes, tool calls
- Better session labels

### 3. Session Search
- Full-text search in transcripts
- Filter by date, model, backend
- Tag-based organization

### 4. Session Backup
- Export sessions to portable format
- Import sessions from other machines
- Cloud sync support

### 5. Thread Expiration Handling
- Detect thread expiration proactively
- Warn user before thread expires
- Auto-refresh threads

## Conclusion

The hierarchical session resolution strategy solves the original problem by:

1. **Checking Codex threads first** - Allows loading external threads
2. **Falling back to MTP sessions** - Preserves existing functionality
3. **Using fuzzy matching** - Improves user experience
4. **Providing helpful errors** - Guides users to success

The implementation maintains conversation continuity through `codex exec resume`, with manual history injection as a robust fallback when threads expire.

This architecture unifies Codex CLI and MTP TUI session management, providing a seamless experience for users while preserving the full power of Codex's stateful conversations.
