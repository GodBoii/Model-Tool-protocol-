# Conversation History Flow - Visual Diagram

## Before Fix (Broken Flow)

```
┌─────────────────────────────────────────────────────────────────┐
│ User sends follow-up query: "what did i asked you above?"       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ TUI: _run_codex_prompt(state, prompt)                           │
│   - Only passes: prompt text                                    │
│   - Passes: previous_session_id (if exists)                     │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Codex Backend: run_codex_prompt()                               │
│   - Builds command: codex exec resume <session_id> <prompt>    │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                    ┌────────┴────────┐
                    │ Session Valid?  │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │ YES          │ NO           │
              ▼              ▼              │
    ┌─────────────┐  ┌──────────────────┐  │
    │ Resume works│  │ Resume FAILS     │  │
    │ ✓ History   │  │ ✗ NO HISTORY     │◄─┘
    │   maintained│  │   (PROBLEM!)     │
    └─────────────┘  └──────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Retry as fresh  │
                    │ session WITHOUT │
                    │ history         │
                    └─────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Response has NO │
                    │ context from    │
                    │ previous turns  │
                    └─────────────────┘
```

## After Fix (Working Flow)

```
┌─────────────────────────────────────────────────────────────────┐
│ User sends follow-up query: "what did i asked you above?"       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ TUI: _run_codex_prompt(state, prompt)                           │
│   ✓ Extracts conversation_history from state.transcript         │
│   ✓ Passes: prompt, session_id, conversation_history            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Codex Backend: run_codex_prompt()                               │
│   - Receives: prompt + conversation_history                     │
│   - Enhanced session ID extraction (Option 2)                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                    ┌────────┴────────┐
                    │ Session ID      │
                    │ exists?         │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │ YES          │ NO           │
              ▼              ▼              │
    ┌─────────────────┐  ┌──────────────────────┐
    │ Try resume with │  │ Fresh session WITH   │
    │ session ID      │  │ history injection    │
    └────────┬────────┘  │ (Option 1)           │
             │           └──────────┬───────────┘
             │                      │
             ▼                      │
    ┌────────┴────────┐             │
    │ Resume works?   │             │
    └────────┬────────┘             │
             │                      │
      ┌──────┼──────┐               │
      │ YES  │ NO   │               │
      ▼      ▼      │               │
    ┌───┐  ┌────────┴──────────┐   │
    │ ✓ │  │ Retry as fresh    │   │
    │   │  │ WITH history      │   │
    │   │  │ injection         │   │
    │   │  │ (Option 1)        │   │
    └───┘  └────────┬──────────┘   │
                    │               │
                    └───────┬───────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │ Build enriched  │
                   │ prompt with     │
                   │ history context │
                   └────────┬────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │ Execute Codex   │
                   │ with context    │
                   └────────┬────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │ Response HAS    │
                   │ full context ✓  │
                   └─────────────────┘
```

## History Injection Process (Option 1)

```
┌─────────────────────────────────────────────────────────────────┐
│ Input: conversation_history = [                                  │
│   ("@README.md tell me what we're working on",                  │
│    "We're working on MTPX..."),                                 │
│   ("Is it open source?",                                        │
│    "Yes, it's MIT licensed...")                                 │
│ ]                                                               │
│ Current prompt: "what did i asked you above?"                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ _build_prompt_with_history()                                    │
│   1. Limit to last N turns (default: 5)                         │
│   2. Truncate long messages                                     │
│   3. Format as structured context                               │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Output: Enriched Prompt                                         │
│                                                                 │
│ [Previous conversation for context:]                            │
│                                                                 │
│ Turn 1:                                                         │
│ User: @README.md tell me what we're working on                  │
│ Assistant: We're working on MTPX...                             │
│                                                                 │
│ Turn 2:                                                         │
│ User: Is it open source?                                        │
│ Assistant: Yes, it's MIT licensed...                            │
│                                                                 │
│ [End of previous conversation]                                  │
│                                                                 │
│ Current query: what did i asked you above?                      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Send to Codex   │
                    │ with full       │
                    │ context         │
                    └─────────────────┘
```

## Session ID Extraction Enhancement (Option 2)

```
┌─────────────────────────────────────────────────────────────────┐
│ Codex JSON Event Stream                                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Event: {"type": "thread.started", "thread_id": "thread-12345"}  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ _extract_codex_session_id(event, event_type)                    │
│                                                                 │
│ BEFORE (Limited):                                               │
│   - Check 4 keys: thread_id, session_id, conversation_id, id   │
│   - Check 5 nested keys                                         │
│   - Only for specific event types                               │
│                                                                 │
│ AFTER (Enhanced):                                               │
│   ✓ Check 9 keys (added: chat_id, context_id, camelCase)       │
│   ✓ Check 8 nested keys (added: data, metadata, context)       │
│   ✓ Added turn.started event type                              │
│   ✓ Fallback for any "started"/"begin" events                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Result: "thread-12345" ✓                                        │
│ (More reliable session ID capture)                              │
└─────────────────────────────────────────────────────────────────┘
```

## State Management Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ TUIState                                                        │
│   - transcript: List[TranscriptTurn]                            │
│   - codex_session_id: str | None                                │
│   - session_id: str (TUI session)                               │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ User Query 1: "@README.md tell me what we're working on"        │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Execute → Get Response                                          │
│   codex_session_id = "thread-abc123" (captured)                 │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ _record_turn(state, prompt, result)                             │
│   transcript.append(TranscriptTurn(...))                        │
│   _save_tui_session(state)                                      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ TUIState (Updated)                                              │
│   - transcript: [Turn1]                                         │
│   - codex_session_id: "thread-abc123"                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ User Query 2: "what did i asked you above?"                     │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ _run_codex_prompt(state, prompt)                                │
│   conversation_history = [(Turn1.prompt, Turn1.response)]       │
│   previous_session_id = "thread-abc123"                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                    ┌────────┴────────┐
                    │ Try resume OR   │
                    │ inject history  │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Response with   │
                    │ context ✓       │
                    └─────────────────┘
```

## Token Usage Comparison

```
BEFORE FIX:
┌──────────────────────────────────────┐
│ Query: "what did i asked you above?" │
│ Tokens: ~10                          │
│ Context: NONE                        │
│ Total: ~10 tokens                    │
└──────────────────────────────────────┘

AFTER FIX (with 2 previous turns):
┌──────────────────────────────────────┐
│ [Previous conversation...]           │
│ Turn 1: ~150 tokens                  │
│ Turn 2: ~120 tokens                  │
│ Current query: ~10 tokens            │
│ Formatting: ~30 tokens               │
│ Total: ~310 tokens                   │
└──────────────────────────────────────┘

Impact: +300 tokens per request
But: Enables proper conversation continuity
Still: Well within 400K token context window
```

## Error Handling Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ Attempt: codex exec resume <session_id>                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                    ┌────────┴────────┐
                    │ Success?        │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │ YES          │ NO           │
              ▼              ▼              │
    ┌─────────────┐  ┌──────────────────┐  │
    │ Return      │  │ Error detected   │  │
    │ result ✓    │  │ (exit code != 0) │  │
    └─────────────┘  └────────┬─────────┘  │
                              │             │
                              ▼             │
                     ┌─────────────────┐    │
                     │ Emit warning:   │    │
                     │ "Session resume │    │
                     │  failed..."     │    │
                     └────────┬────────┘    │
                              │             │
                              ▼             │
                     ┌─────────────────┐    │
                     │ Build prompt    │    │
                     │ WITH history    │    │
                     └────────┬────────┘    │
                              │             │
                              ▼             │
                     ┌─────────────────┐    │
                     │ Retry as fresh  │    │
                     │ session         │    │
                     └────────┬────────┘    │
                              │             │
                              ▼             │
                     ┌─────────────────┐    │
                     │ Success with    │    │
                     │ history ✓       │    │
                     └─────────────────┘    │
```

## Summary

The fix implements a **resilient conversation history system** with:

1. **Primary Path**: Codex native session resume (fast, efficient)
2. **Fallback Path**: Manual history injection (reliable, always works)
3. **Enhanced Detection**: Better session ID capture (fewer failures)

This ensures conversation continuity regardless of Codex session state.
