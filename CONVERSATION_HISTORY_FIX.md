# Conversation History Fix - Implementation Documentation

## Problem Statement

The TUI CLI was not maintaining conversation history between follow-up queries. When users sent a follow-up question like "what did I ask you above?", the system would respond as if it was a completely new conversation with no prior context.

### Root Cause

The issue occurred in the Codex backend integration:

1. **Session Resume Failure**: The TUI relied on `codex exec resume <session_id>` to maintain conversation history
2. **No Fallback Mechanism**: When session resume failed (expired sessions, invalid IDs), the system would start a fresh session WITHOUT manually injecting the conversation history
3. **Limited Session ID Extraction**: The session ID extraction logic was too narrow and missed various ID formats returned by Codex

## Solution Overview

We implemented a **two-pronged approach**:

### Option 1: Manual History Injection
When Codex session resume is unavailable or fails, we manually inject conversation history into the prompt.

### Option 2: Enhanced Session ID Extraction
Improved the session ID extraction logic to capture more ID formats and patterns from Codex events.

## Implementation Details

### 1. History Injection Function (`_build_prompt_with_history`)

**Location**: `src/mtp/cli/tui_codex_backend.py`

**Purpose**: Constructs a prompt that includes previous conversation turns for context.

**Features**:
- Limits history to last N turns (default: 5) to avoid token overflow
- Truncates very long messages (user: 500 chars, assistant: 800 chars)
- Formats history in a clear, structured way
- Preserves the current query at the end

**Example Output**:
```
[Previous conversation for context:]

Turn 1:
User: @README.md tell me what are we working on
Assistant: We're working on MTPX (Model Tool Protocol Extended)...

[End of previous conversation]

Current query: what did i asked you above?
```

### 2. Enhanced Session ID Extraction (`_extract_codex_session_id`)

**Location**: `src/mtp/cli/tui_codex_backend.py`

**Improvements**:
- Expanded candidate keys: Added `chat_id`, `context_id`, `threadId`, `sessionId`, `conversationId`
- Added `turn.started` to recognized event types
- Checks more nested structures: `data`, `metadata`
- Fallback logic for any "started" or "begin" events

**Before**:
```python
candidate_keys = ("thread_id", "session_id", "conversation_id", "id")
nested_keys = ("thread", "session", "conversation", "payload", "item")
```

**After**:
```python
candidate_keys = (
    "thread_id", "session_id", "conversation_id", "chat_id", 
    "context_id", "id", "threadId", "sessionId", "conversationId"
)
nested_keys = (
    "thread", "session", "conversation", "context", 
    "payload", "item", "data", "metadata"
)
```

### 3. Updated `run_codex_prompt` Function

**Location**: `src/mtp/cli/tui_codex_backend.py`

**New Parameter**: `conversation_history: list[tuple[str, str]] | None = None`

**Logic Flow**:
1. Check if session ID exists
2. If NO session ID AND history available → inject history into prompt
3. Execute Codex command with resume (if session ID) or fresh (if no session)
4. If resume FAILS → retry with history injection as fallback
5. Return result with updated session ID

**Key Code**:
```python
# Determine if we should inject history
should_inject_history = (not previous_session_id) and conversation_history

# Build prompt with history if needed
effective_prompt = prompt
if should_inject_history:
    effective_prompt = _build_prompt_with_history(prompt, conversation_history or [])
    if emit_live:
        emit_live("status", f"Injecting {len(conversation_history or [])} previous turns for context")
```

**Retry Logic with History**:
```python
if return_code != 0 and previous_session_id:
    # Session resume failed, retry with history injection
    if emit_live:
        emit_live("warn", "Session resume failed, retrying with conversation history injection")
    
    retry_prompt = prompt
    if conversation_history:
        retry_prompt = _build_prompt_with_history(prompt, conversation_history)
```

### 4. TUI Integration (`_run_codex_prompt`)

**Location**: `src/mtp/cli/tui.py`

**Changes**:
- Extract conversation history from `state.transcript`
- Pass history to `codex_backend.run_codex_prompt()`

**Code**:
```python
# Build conversation history from transcript
conversation_history: list[tuple[str, str]] = []
for turn in state.transcript:
    conversation_history.append((turn.prompt, turn.response))

codex_result = codex_backend.run_codex_prompt(
    codex_bin=codex_bin,
    cwd=state.cwd,
    prompt=prompt,
    model=state.codex_model,
    reasoning_effort=state.reasoning_effort,
    previous_session_id=state.codex_session_id,
    conversation_history=conversation_history,  # ← NEW
    emit_live=_emit_live_event,
)
```

## Behavior Matrix

| Scenario | Session ID | History Available | Behavior |
|----------|-----------|-------------------|----------|
| First query | None | No | Fresh Codex session, no history |
| Follow-up (resume works) | Valid | Yes | Use `codex exec resume`, history maintained by Codex |
| Follow-up (resume fails) | Invalid/Expired | Yes | Retry with manual history injection |
| New session | None | Yes | Fresh session with manual history injection |

## Testing

### Test Coverage

**Test File**: `test_conversation_history_fix.py`

**Test Cases**:
1. **History Injection Tests**:
   - No history → returns original prompt
   - Single turn → correctly formatted
   - Multiple turns → all turns included
   - Max turns limit → only last N turns
   - Long messages → truncated properly

2. **Session ID Extraction Tests**:
   - Standard formats (thread_id, session_id)
   - Nested structures
   - CamelCase formats
   - Alternative keys (chat_id)
   - Turn started events
   - No ID present

3. **Integration Test**:
   - Simulates real conversation flow
   - Verifies context preservation

### Running Tests

```bash
python test_conversation_history_fix.py
```

**Expected Output**: All tests pass ✅

## User Experience Improvements

### Before Fix
```
User: @README.md tell me what are we working on
Assistant: We're working on MTPX...

User: what did i asked you above?
Assistant: I don't have information about previous questions.
```

### After Fix
```
User: @README.md tell me what are we working on
Assistant: We're working on MTPX...

User: what did i asked you above?
Assistant: You asked me to tell you what we're working on, 
          referencing the README.md file.
```

## Performance Considerations

### Token Usage
- History injection adds tokens to each request
- Limited to last 5 turns by default (configurable)
- Messages truncated to prevent excessive token usage
- User messages: max 500 chars
- Assistant messages: max 800 chars

### Estimated Token Impact
- Average turn: ~100-200 tokens
- 5 turns: ~500-1000 additional tokens per request
- Still well within context windows (400K tokens for gpt-5.x models)

## Configuration

### Adjusting History Depth

To change the number of turns included in history, modify the `max_turns` parameter:

```python
# In tui_codex_backend.py
effective_prompt = _build_prompt_with_history(
    prompt, 
    conversation_history or [], 
    max_turns=3  # Change from default 5 to 3
)
```

### Adjusting Truncation Limits

To change message truncation limits:

```python
# In _build_prompt_with_history function
user_truncated = user_msg[:1000] + "..." if len(user_msg) > 1000 else user_msg
assistant_truncated = assistant_msg[:1500] + "..." if len(assistant_msg) > 1500 else assistant_msg
```

## Monitoring & Debugging

### Live Event Notifications

When history is injected, users see:
```
status: Injecting 3 previous turns for context
```

When session resume fails:
```
warn: Session resume failed, retrying with conversation history injection
```

### Checking Session State

Use `/status` command in TUI to see:
- Current session ID
- Number of turns in transcript
- Whether Codex session is active

## Future Enhancements

### Potential Improvements

1. **Smart History Selection**
   - Use semantic similarity to select most relevant turns
   - Instead of just last N turns

2. **Compression**
   - Summarize older turns to save tokens
   - Keep recent turns in full detail

3. **Hybrid Approach**
   - Try resume first
   - Inject minimal history as backup
   - Full history only if needed

4. **Session Persistence**
   - Store Codex session IDs more reliably
   - Implement session refresh mechanism
   - Handle session expiration gracefully

## Troubleshooting

### Issue: History not appearing in responses

**Check**:
1. Verify `state.transcript` has entries: `/status` → check "turns"
2. Look for "Injecting X previous turns" message
3. Check if session resume is working (no "resume failed" warning)

**Solution**: If session resume works, history is maintained by Codex. If it fails, history injection should activate.

### Issue: Token limit errors

**Symptoms**: Errors about context length exceeded

**Solution**: Reduce `max_turns` parameter in history injection

### Issue: Session ID not being captured

**Check**: Look at Codex JSON event output for session/thread IDs

**Solution**: Add additional ID key patterns to `_extract_codex_session_id`

## Related Files

- `src/mtp/cli/tui_codex_backend.py` - Core backend logic
- `src/mtp/cli/tui.py` - TUI integration
- `test_conversation_history_fix.py` - Test suite
- `docs/CLI.md` - CLI documentation

## Conclusion

This fix ensures that follow-up queries in the TUI maintain conversation context through a robust two-layer approach:

1. **Primary**: Codex native session resume (when available)
2. **Fallback**: Manual history injection (when resume fails or unavailable)

The implementation is backward compatible, adds minimal overhead, and significantly improves the user experience for conversational interactions.
