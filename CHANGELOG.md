# MTP CLI and TUI improvement program

## Release 0.1.40, pending publication

- Add opt-in MCP 2026-07-28 discovery and per-request metadata alongside the
  legacy handshake, with JSON Schema 2020-12 validation and no external fetches.
- Add stateless JSON Streamable HTTP, strict mirrored headers, bearer-only HTTP
  credentials, body deadlines, and concurrent stdio cancellation. Add the
  `mcp-streamable-http` CLI scaffold and `mcp-modern` installation extra.
- Add opt-in Docker Python execution with no host mounts, disabled networking,
  restricted privileges, bounded resources/output, and timeout cleanup.
- Harden compatible-provider endpoints, option ownership, Responses media
  rejection, and malformed native call/stream handling.
- Avoid reverse DNS during modern HTTP startup and wait for observable TUI
  conversation readiness in tests on slower runners.

## Release 0.1.39, 2026-10-05

- Add Hugging Face Inference Providers, DeepInfra, DashScope, and a configurable
  OpenAI-compatible Chat Completions adapter with native streaming.
- Add opt-in OpenAI Responses with stateless replay of output items, encrypted
  reasoning, and function-call IDs through saved sessions.
- Integrate hosted endpoints, credentials, regional URLs, output budgets, and
  model catalogs into CLI/TUI setup; preserve Hugging Face routing suffixes.
- Reject incomplete tool streams and duplicate IDs before execution. Keep every
  identical read's dependency and approval checks; retain explicit TTL caching.
- Verify new contracts against the installed SDK's JSON/SSE handling on
  loopback servers and the compatible adapter against authorized live Groq.
- Reject executable paths before platform-specific shell parsing and wait for
  observable TUI readiness in tests on slower CI runners.

## Release 0.1.38, 2026-10-05

- Fix all 18 reproduced audit defects, including write multiplicity, failed
  dependencies, prior-round references, complete tool history, and async streams.
- Preserve provider tool identities and opaque reasoning state across saved
  sessions; isolate native Fireworks client credentials.
- Validate website redirects and MCP HTTP origins, version negotiation, and
  JSON-RPC method errors. Declare supported legacy MCP revisions explicitly.
- Add Groq output budgets, refresh shared defaults, correct Cerebras extras,
  and raise the Python floor to 3.11. CI installs its async runner and requires
  nonempty integration tests.
- Default Python tools restrict imports, attribute access, and builtins. SDK TUI
  workspace permissions do not automatically authorize arbitrary shell commands.
  Full Python and unrestricted commands require explicit opt-in.

## Release 0.1.37, 2026-09-30

- Improve provider setup, command selection and chat navigation in the TUI.
- Keep setup actions visible and reflow output when the terminal resizes.
- Align CLI credentials, reject masked keys and validate local provider setup and startup options.
- Discover provider models and support custom model IDs without retired Groq defaults.
- Preserve chat views during background notices and update the TUI setup documentation.

## Release 0.1.36, 2026-09-30

- Execute completed slash-command selections without repeating the selection loop.
- Discover current Codex models, reasoning options and subscription limits.
- Update CLI documentation for command selection and Codex capabilities.

## Release 0.1.35, 2026-09-30

- Publish the current SDK, CLI and TUI code as `mtpx` 0.1.35.
- Keep the package metadata, Python version and default MCP server version aligned.
- Include the latest fixes for chat-specific codebase scans, lossless streaming in Agent OS and transcript context when rebuilding agents.

This file records the plan for improving the `mtp tui` terminal app, what has shipped so far, and what is left. It covers UI, UX, motion, features, shortcuts, multitasking, speed, latency and robustness.

The previous release notes, 0.1.6 through 0.1.15, are still in git history. Run `git show fe251da:CHANGELOG.md` to read them.

- Baseline commit: `fe251da` "Add MTP docs frontend URL"
- Work so far: 19 commits on `main`, `ab54098` through `f271e15`
- Test suite: `python -m pytest -m "not integration and not live"` passes with 546 tests, with 11 deselected. Before this work it had 400.
- Environment used: Windows, Python 3.13.3, Textual 8.2.5, Rich 14.3.3

Contents:

1. Where the TUI started
2. The full plan, phase by phase
3. What is done, phase by phase and commit by commit
4. What is remaining, in detail
5. How to verify the remaining work
6. Decisions already made by the project owner

---

## 1. Where the TUI started

`mtp tui` is a Textual app. `src/mtp/cli/main.py` parses arguments. `src/mtp/cli/tui.py` builds a `TUIState` and launches `MTPApp` from `src/mtp/cli/tui_app.py`. Widgets live in `src/mtp/cli/tui_widgets/`. There are two kinds of backend:

- `codex` runs the official Codex CLI as a subprocess, through `codex exec`, and parses its JSON event lines. The code is in `tui_codex_backend.py`.
- Every other backend is an MTP SDK provider such as openai, groq, claude, gemini, openrouter, mistral, cohere, sambanova, cerebras, deepseek, togetherai, fireworksai, xiaomi, ollama or lmstudio. These go through `Agent.run_events` and `tui_mtp_backend.py`.

A review of the code at the baseline found these problems. Every line reference is from `fe251da`.

**Correctness bugs**

- `_merge_stream_text` in `tui_mtp_backend.py` threw away any streamed chunk that already appeared earlier in the text. Tokens like `" the"`, `","` and newlines vanished from saved replies. The live view used plain concatenation, so a reply could change once the turn finished.
- `_send_prompt` started every run with `run_worker(..., exclusive=True)` and did not check whether a run was already going. A second prompt cancelled the asyncio wrapper but not the thread doing the work, and the old thread kept pushing events into the new turn.
- The codebase scan worker also used `exclusive=True` in the default worker group, so starting a scan could cancel the LLM run.
- Live rendering was throttled to one frame per 100 ms with no trailing flush, so the last chunks of a burst could stay unrendered.
- `/load` changed state but never redrew the chat, so the loaded transcript did not appear.
- Esc could not cancel Codex runs, the Codex subprocess was never killed, and Codex output did not stream at all.
- Provider errors were turned into a normal reply with the text "Error: ...". Worker crashes dropped the turn entirely.
- A corrupt `tui_provider_settings.json` was silently replaced with empty settings on the next save, which deleted every API key.
- The as-you-type suggestion handler was named `on_input_area_changed`, but Textual sends `TextArea.Changed` to `on_text_area_changed`. The handler never ran, so suggestions only appeared after pressing Tab.

**Performance problems**

- `AssistantMessageWidget.update_message` removed and remounted every child widget, and re-parsed the whole reply as Markdown, up to 10 times a second. Cost grew with reply length, and tool spinners restarted their timers on every frame.
- `_rebuild_chat_log` re-rendered the whole transcript after every turn.
- `call_from_thread` sent one blocking call per token, so the model stream waited on the UI.
- `active_model_name()` read the settings JSON from disk on every render and every status refresh.
- `@file` suggestions ran `os.walk` on every keystroke, and `/load` suggestions parsed all of `sessions.json` on every keystroke.
- `save_tui_session` rewrote the whole `sessions.json` on the UI thread after almost every command, and twice per turn.
- `/backend` built the provider and agent on the UI thread, and `/codex status` and `/codex account` ran subprocesses there.
- `main.py` imported every subcommand at module level, and `import mtp` loaded the whole SDK, including the agent loop, all providers, toolkits and MCP.
- The input was focused only after a fixed 0.5 s timer.

**UX gaps**

- One session at a time. No tabs and no background runs.
- No queue: sending during a run broke the run, as described above.
- No steering, and no way to add information to a reply while it runs.
- Shortcut hints were hardcoded in three places. Slash commands were defined in three separate lists that had drifted apart.
- No motion or transitions beyond spinners.
- No in-app provider setup. The TUI told users to type `/apikey set`.

---

## 2. The full plan, phase by phase

The phases run in order, because later phases depend on earlier ones. Phases 1 to 3 make the app correct and fast, and the new features are built on top of that. For each phase below: goal, steps, files, and how to know it is done.

### Phase 0. Safety net

**Goal.** Make regressions visible before changing behavior.

**Steps.**

1. Add Textual pilot tests that drive the real `MTPApp` through `App.run_test()`. Replace the model call with a fake `run_prompt_blocking` that the test controls step by step: it records calls, blocks until released, and hands back the `emit_callback` so the test can push live events from a thread.
2. Add unit tests for stream merging that cover repeated tokens, the case that was failing.
3. Record an import-time baseline with `python -X importtime` and wall-clock timing.
4. Optionally add a frame-time counter behind an `MTP_TUI_DEBUG` environment variable.

**Done when** each Phase 1 bug has a test that fails on the baseline code and passes after the fix.

### Phase 1. Correctness

**Goal.** Every run is isolated, every chunk renders, and nothing is lost.

**Steps.**

1. Stream merging: append deltas, and deduplicate only real full-payload replays.
2. Run identity: give every run an id, and drop live events whose run id is not current.
3. Ignore state changes from superseded workers.
4. Separate worker groups for LLM runs and codebase-memory jobs.
5. A trailing flush timer for throttled rendering.
6. `/load` redraws the chat.
7. Cancelable Codex runs that kill the whole process tree, plus Codex live events.
8. The five behaviors that needed the owner's decision:
   - what happens when a prompt is sent during a run
   - the interrupt key
   - how failed and cancelled turns are stored
   - how a corrupt settings file is handled
   - what state-changing commands do during a run

   The owner decided these; see section 6.

**Files.** `tui_app.py`, `tui_mtp_backend.py`, `tui_codex_backend.py`, `tui_workers.py`, `tui_settings.py`.

### Phase 2. Rendering speed

**Goal.** Frame cost depends on what changed, not on how long the reply or session is.

**Steps.**

1. Update the assistant message widget incrementally: match blocks by position and type, update widgets in place, and mount only new blocks.
2. Split Markdown at a stable point. Render finished paragraphs once and re-parse only the last paragraph.
3. Batch events on the worker side and post them without blocking.
4. Append-only chat log: the live widget becomes the finished turn, with no rebuild.
5. Session windowing: mount only the newest turns and load older ones on demand.
6. One shared spinner clock for all running tool rows.

**Files.** `tui_widgets/chat_log.py`, a new `tui_widgets/stream_markdown.py`, a new `tui_live_events.py`, `tui_app.py`.

### Phase 3. Take I/O off the UI thread

**Goal.** No disk, subprocess or network work on the UI thread during normal use.

**Steps.**

1. Cache settings, keyed by the file's mtime and size, and write them atomically.
2. Save sessions debounced on a background writer and flush on exit.
3. Build background indexes for `@file` and session suggestions.
4. Run `/backend`, `/codex status`, `/codex account` and attachment reads off the UI thread.
5. Longer term, replace the single `sessions.json` with per-session storage. This is still remaining; see section 4.

**Files.** `tui_settings.py`, a new `tui_persistence.py`, a new `tui_indexes.py`, `tui_workers.py`, `tui_app.py`.

### Phase 4. Startup

**Goal.** Fast first paint and instant typing.

**Steps.**

1. Import subcommands lazily in `main.py`.
2. Import public names lazily in `mtp/__init__.py`.
3. Focus the input on the first frame.
4. Start the codebase-memory status check after the first paint.
5. Cheaper first render for long resumed sessions.

**Files.** `cli/main.py`, `mtp/__init__.py`, `agent.py`, `tui_app.py`, `chat_log.py`, `stream_markdown.py`.

### Phase 5. Shortcuts and commands

**Goal.** One source of truth for commands and keys, and a complete keyboard workflow.

**Steps.**

1. A `CommandSpec` registry that drives the parser, autocomplete, the command palette, `/help` and the docs. A docs test checks it against `docs/CLI.md`.
2. Complete key bindings for scrolling, jumping between messages, history search, editing in an external editor, toggling all thinking blocks, and a help overlay.
3. Prompt history saved per project.
4. Backend-aware completion for `/model`, and a `/models` list for the active provider.
5. Message actions: copy, copy a code block, retry, edit and resend, fork a session from a message.

### Phase 6. Multi-state and multitasking

**Goal.** Several chats at once, each with its own run, without losing anything.

**Steps.**

1. A per-conversation model.
2. Tabs, with background runs that keep streaming.
3. A prompt queue.
4. Steering on backends that support it.
5. Unread and failure markers, plus toasts.
6. A background tasks panel.
7. A cap on concurrent runs.
8. Live run metrics in the status bar.
9. Restore open chats after a restart.

### Phase 7. Motion and visual polish

**Goal.** Motion that tells you something and costs almost nothing.

**Steps.**

1. Smooth scroll to the bottom, but only when you are already there, plus a "jump to latest" pill.
2. Animated collapse and expand for thinking and tool blocks.
3. Fade-in for fresh text.
4. Input border pulse and color by run state.
5. Toasts for background events.
6. A faster boot screen.
7. Themes.
8. Diff rendering for edit tools.
9. A reduced-motion setting.

### Phase 8. Onboarding and cleanup

**Goal.** First-run setup without guesswork, and a codebase that is easy to change.

**Steps.**

1. A provider setup modal.
2. Masked API keys by default.
3. Split `tui_app.py` into smaller modules.
4. Delete dead code and duplicates.
5. Archive stale fix documents.
6. Pin Textual to the 8.x series.
7. Fix documentation drift.

---

## 3. What is done

Each item below lists the problem, the change, the files, the tests and the measurements where they exist.

### Phase 0. Done

- `tests/test_tui_app_lifecycle.py` holds `FakeRunner`, `_make_state` and the `fake_runner` fixture. The other TUI test files import them.
  - `FakeRunner` records each call's prompt, emit callback, run id, release event and Codex handle, and returns a fixed `ChatResult`.
  - If a Codex handle was cancelled, `FakeRunner` returns a cancelled result, matching what `_run_codex` reports.
- `tests/test_tui_stream_merge.py` covers the merge rules and runs `run_mtp_prompt` against a fake agent.
- For each Phase 1 fix, the new test was run against the baseline `tui_app.py` to confirm it fails there. The first throttle test did not fail on the old code, because its two events arrived more than 100 ms apart. It was rewritten until it caught the bug.
- Baseline import timings: `mtp.cli.main` took about 340 ms, and `mtp.cli.tui_app` added about 205 ms on top.

### Phase 1. Done

**`ab54098` fix(tui): stop dropping repeated streamed tokens**

- Problem: `_merge_stream_text` returned the existing text whenever the new chunk already appeared inside it, and it also ran an up-to-4000-character overlap scan on every chunk. In a reproduction, three reasoning chunks `"step"`, `" step"`, `" step"` were saved as `"step"`.
- Change: deltas are always appended. A payload counts as a replay only if the existing text is at least 32 characters long and the new chunk is either identical to it or starts with all of it. `_append_unique_text` no longer rebuilds the joined string on every chunk.
- File: `src/mtp/cli/tui_mtp_backend.py`.
- Tests: `tests/test_tui_stream_merge.py`, 7 tests. Five of them failed before the fix.

**`a0bd55e` fix(tui): isolate runs from stale events and memory jobs**

- Live events carry the run id and are dropped when it is not the active run.
- The worker-finished handler ignores workers that are not the current run's worker.
- LLM runs use the `llm` worker group and codebase jobs use the `memory` group, so `exclusive=True` cannot cross between them.
- Scan start and finish no longer hide or reset the spinner of an LLM run.
- The renderer schedules a trailing timer when throttled, so the last chunk always renders.
- `/load` rebuilds the chat log and refreshes the sidebar.
- Tests: 4 pilot tests in `tests/test_tui_app_lifecycle.py`. Three of them failed on the old code.

**`63dc73e` feat(tui): cancel and stream codex runs**

- `CodexRunHandle` in `tui_codex_backend.py` holds the running `Popen` under a lock.
  - `cancel()` kills the process tree: `taskkill /PID <pid> /T /F` on Windows, and a signal to the process group on POSIX, where the process starts with `start_new_session=True`.
  - This matters on Windows because `codex` resolves to `codex.cmd`. Killing only that shim leaves the node child running and holding the stdout pipe open.
  - Cancelling before the process starts kills it as soon as it attaches.
- A cancelled resume no longer triggers the "resume failed, retry as a fresh session" path.
- `_run_codex` passes `emit_live` and the handle, so Codex status and tool events reach the TUI.
- Tests: `tests/test_tui_codex_cancel.py`, 3 tests.
  - One uses a real Python parent process that starts a sleeping grandchild sharing stdout. The test proves the reader unblocks after cancel and that no orphan processes remain.
  - The others cover cancel-before-start and no retry after cancel.
  - A pilot test checks the Esc wiring.

**`1671b6c` fix(tui): back up a corrupt provider settings file and warn** (the owner's decision 4)

- `_parse_settings` now raises on invalid JSON, a top level that is not an object, or a `providers` value that is not an object.
- `load_provider_settings` then:
  - copies the file to `tui_provider_settings.json.corrupt-YYYYmmdd-HHMMSS`, with a numeric suffix if that name exists
  - records a `SettingsRecovery` notice and returns empty settings
  - caches that result under the corrupt file's signature, so the copy is made only once
- The app pops these notices in `_refresh_status_bar` and shows a 20-second warning toast that names the backup file.
- A settings file with no `providers` key is not treated as corrupt; the key is added.
- Tests: 3 new unit tests in `tests/test_tui_settings_cache.py` and a pilot test that checks the toast.

**`271a93b` feat(agent): steer active runs and repair history after a crash** (supports decisions 1 and 3)

- `Agent.steer_run(run_id, text)` queues a user message under a lock. At the start of the run's next model round, `_apply_steering` adds it to the history as `[Update from the user while you were working] <text>`. This happens in all four round loops: sync, async, and both event-stream versions.
- The event loops emit `steer_applied` with the applied texts.
- `take_unapplied_steering(run_id)` returns messages that arrived after the last round, so the caller can run them next. `steer_run` returns False once the run has ended; `_complete_run` holds the same lock, which closes that race.
- `_register_run` calls `_repair_dangling_tool_calls()` before every run.
  - A run that failed or was killed between the assistant's tool-call message and the tool results leaves calls without answers.
  - Most chat APIs reject that history, so the next prompt would fail too.
  - Each unanswered call gets a placeholder tool result: "Not run: the previous run stopped before this tool call finished."
- `MTPAgent` in `simple_agent.py` passes both new methods through.
- Tests: `tests/test_agent_steering.py`, 5 tests, with their own stub providers. They do not depend on `tests/conftest.py`, which is not tracked in git.

**`b5c7133` feat(tui): keep failed and cancelled turns and let the chat continue** (decision 3)

- `tui_state.py` adds `TURN_COMPLETED`, `TURN_FAILED` and `TURN_CANCELLED`.
  - `ChatResult` gains `status`, `error` and `unapplied_steering`.
  - `TranscriptTurn` gains `status`, `error` and `history_reply()`. That method adds "[This reply was interrupted by the user.]" or "[This reply failed: ...]" when the turn is used as history.
  - Sessions saved before this change load as completed.
- `run_mtp_prompt` wraps the event stream in `_stream_until_failure`.
  - A provider exception ends the stream and is recorded, and the text, thinking and tool rows streamed so far are kept.
  - A `run_cancelled` event marks the turn cancelled.
  - The result includes any unapplied steering.
- `_run_mtp` and `_run_codex` report failures as `status="failed"` with an error message instead of "Error: ..." text. A Codex non-zero exit counts as failed, and a cancelled handle counts as cancelled. Codex history uses `history_reply()`.
- The app records every outcome through one finish path.
  - A worker crash builds a failed turn from the live preview.
  - All `run_worker` calls use `exit_on_error=False`. Before this, a crash in any worker, including the codebase scan, closed the whole app.
- `chat_log.py` renders "x Run failed: <error>  Send a message to continue." or "- Interrupted; output above is partial." under the turn.
- Tests: `tests/test_tui_turn_status.py`, 6 tests.

**`bffa587` feat(tui): parallel chats, queued messages, steering and Ctrl+X** (decisions 1, 2 and 5)

This is the multitasking refactor. It is a single commit because it changes the core structure of the app, and a split would have left `main` broken between commits.

- A new `src/mtp/cli/tui_conversation.py`:
  - `LiveTurn` holds one in-flight turn: prompt, attachments, the backend and model it started with, blocks, thinking, tool events and details, warnings, steered messages, render timing, the flush timer, and the widgets mounted for it. It also holds the block-building logic that used to live on the app.
  - `ConversationView` is one chat log plus its own run spinner.
  - `QueuedPrompt` is one queued message with an id.
  - `Conversation` holds a `TUIState`, its view, the current `LiveTurn`, run id, worker, Codex handle, queue, unread flag and last status.
- `MTPApp` holds a list of conversations and the active one.
  - `_state` and `state` are now properties that return the active conversation's state, so commands act on the chat on screen.
  - Live events are routed by run id and worker results by worker identity.
  - Each conversation's runs use their own worker group, `llm-<id>`, so starting a run in one chat can never cancel another.
- Layout: a `Tabs` bar, then a `ContentSwitcher` of conversation views, then a separate `#task-spinner` for codebase jobs, then the command log, then the input panel.
- Tab labels show the position, the title, `⋯` while running, `●` for a new reply, a red `!` for a failed background run, and `+N` for queued messages.
- Commands:
  - `/new`, or `Ctrl+N`, opens a new chat that inherits the backend and settings.
  - `/load` opens a saved session in its own chat, or switches to it if it is already open.
  - `/switch <n>`, `Alt+1..9`, `Alt+Left/Right` and `Ctrl+PageUp/PageDown` switch chats.
  - `/tabs` lists chats.
  - `/close [n]` closes a chat. It refuses while that chat is running, and it refuses to close the last chat.
- Queue:
  - `Enter` during a run adds to the queue, and the queue bar lists items with "remove" and "steer now" click actions.
  - The next item starts automatically when the run finishes, whether it completed, failed or was cancelled.
  - `/queue` lists the queue and `/queue clear` empties it.
- Steering:
  - Allowed only when the chat is running, the backend is not Codex, and the agent has `steer_run`.
  - `Ctrl+G` steers the newest queued item. `/steer <text>` steers directly, queues if steering is not possible, or starts a run if nothing is running.
  - A steered message appears inside the reply as "↳ You, mid-run: ..." and is saved in the turn's blocks.
  - Unapplied steering goes to the front of the queue with a note.
- `Ctrl+X` stops the active chat's run.
  - `check_action` returns False when nothing is running, so the key falls through to the input and cuts the selection. A throwaway app confirmed that fallthrough before it was relied on.
  - `Ctrl+C` is untouched.
  - `Esc` closes the suggestion list first and stops the run only when no list is open.
- Turns are recorded with the backend and model they started with. `record_turn` gained `backend=` and `model=`.
- Background completions show a toast.
- `tui_shortcuts.py` is the single list behind the hint bar, the sidebar and `/help`. A test checks that every listed key has a binding; it caught `Esc` against Textual's `escape` key name.
- Stray `_reset_live_preview()` calls in the `/codebase` and `/model` handlers would have wiped a running turn's display, and were removed.
- A bug found and fixed along the way:
  - The first version animated a spinner in the tab label every 0.12 s.
  - Textual's `Tabs` restarts its underline animation on every relabel, which kept the app permanently busy. Pilot key presses then hung until every run finished.
  - Tab labels now use a static marker and are relabelled only when their text changes.
- Tests: `tests/test_tui_multitasking.py`, 11 tests at this commit:
  - two chats streaming at once
  - queue then auto-run
  - `Ctrl+G` steering
  - Codex chats only queue
  - late steering runs next
  - `Ctrl+X` stops versus cuts
  - Esc order
  - close rules
  - the backend label after switching mid-run
  - new-chat settings and `Alt+1`
  - shortcut bindings

**`c2cc88a` fix(tui): keep the chat visible with tabs and a queue**

- A headless screenshot showed the `Tabs` widget taking the whole screen, because of `height: auto`, with the chat squeezed to one row. It also showed the queue bar behind the docked input panel.
- Tabs are now fixed at 2 rows, and the queue bar sits inside `InputPanel`, directly above the prompt.
- A layout test checks that the tab bar is 2 rows, the chat log is at least 15 rows at 110x34, and the queue bar is inside the input panel.

### Phase 2. Done

**`5e12073` feat(tui): add incremental streaming markdown widget**

- `stable_prefix_end(text)` finds the end of the last blank line outside a code fence. It supports backtick and tilde fences, fence length, and a last line without a newline, which may still grow.
- `StreamingMarkdown` renders the text before that point as frozen segments, once. It re-parses only the tail. If the text is replaced rather than extended, it starts over.
- Textual's own `Markdown.append` exists in 8.2.5, but it keeps a full Markdown widget tree per reply. The custom widget keeps the cheap Rich `Markdown` renderables the app already used.
- Tests: `tests/test_tui_stream_markdown.py`, 8 tests, including one for text passed in the constructor.

**`8007dd0` perf(tui): update chat widgets in place instead of remounting**

- `AssistantMessageWidget.update_message` keeps a list of block widgets.
  - It finds how many existing blocks still match by position and type, updates those in place, removes the rest, and mounts only new blocks, before the footer.
  - Thinking blocks keep the user's expand or collapse choice until the turn ends.
- Tool rows share one `SpinnerClock`, owned by the `ChatLog`, which runs only while some tool is running. A row re-renders its result preview only when the preview changes; previews can be 12k characters of JSON.
- A finished turn reuses the live widget through `finalize_live_assistant_message` instead of rebuilding the transcript.
- Long sessions mount a window of recent turns. An `EarlierTurnsButton` at the top loads more on click, or when you scroll to the top.
- Live frames use the model name resolved at run start instead of reading settings.
- Tool events update only the tool list in the sidebar. The workspace tree re-lists only when the working directory changes or the sidebar opens.
- A gotcha found here: Textual sets `is_mounted` only after `on_mount` returns. Guards that checked it dropped the first render. Widgets now keep their own ready flag.
- Tests: `tests/test_tui_render.py`, 3 tests:
  - the same widget instances survive streaming
  - the clock stops after tools finish
  - history is not rebuilt, windowing, and paging

**`60088cb` perf(tui): batch live events without blocking the model stream**

- Textual's `post_message` is thread-safe; it uses `call_soon_threadsafe`, which was confirmed in the 8.2.5 source.
- `LiveEventBatcher` in `tui_live_events.py`:
  - merges adjacent `text` and `reasoning` chunks
  - flushes any other event immediately so tool rows appear right away, keeping order
  - runs a 30 ms flusher thread so text still arrives when the stream stalls
  - flushes on close, before the worker result is posted
- The app handles `LiveEventBatch` messages.
- Tests: `tests/test_tui_live_events.py`, 5 tests, including 2000 tokens pushed through while a flusher runs every 1 ms.

### Phase 3. Done

**`20d3cc1` perf(tui): cache provider settings and write them atomically**

- A cache keyed by the file's `(mtime_ns, size)`. Callers modify what they get back, so it returns deep copies.
- Saves go through a temp file and `os.replace`, then update the cache.
- Tests: 5 in `tests/test_tui_settings_cache.py`, including a parse counter, copy independence, picking up external edits, and atomic writes.

**`2bea692` perf(tui): save sessions debounced on a background thread**

- `save_tui_session` is split in two:
  - `snapshot_tui_session` builds the metadata with no I/O and runs on the UI thread.
  - `write_session_snapshot` merges into the stored record and can run on any thread.
- `SessionSaver` in `tui_persistence.py`:
  - keeps the newest snapshot per session id
  - writes 0.5 s after the last change, on one writer thread, so writes never overlap
  - runs extra work such as codebase conversation summaries on the same thread
  - flushes on `on_unmount` and again after `app.run()` returns
  - reports write errors through a `SessionSaveFailed` message and an error toast
- `record_turn(persist=False)` lets the app save once per turn instead of twice.
- Tests: `tests/test_tui_persistence.py`, 6 tests, plus 2 pilot tests. One proves every write happens on the writer thread; the other proves a change made just before exit is saved.

**`4b7022d` perf(tui): serve autocomplete from background indexes**

- `BackgroundIndex[K, T]` in `tui_indexes.py`:
  - `get(key)` never blocks. It returns the cached value or `None` and starts a daemon rebuild when the key changed or the value expired.
  - On failure it keeps serving the old value.
  - `on_ready` posts `IndexReady`, and the app then refreshes suggestions if the user is still typing an `@` path or a session command.
- `scan_workspace_files` follows the old walk rules: two levels deep, hidden and vendored directories skipped, capped at 20,000 files. The index expires after 30 s and is invalidated after a tool that edits files.
- Session summaries are keyed by the `sessions.json` path, mtime and size.
- Fixed the handler name bug described in section 1, so suggestions now appear as you type.
- Tests: `tests/test_tui_indexes.py`, 6 tests, and a pilot test for file suggestions.

**`7b6073a` perf(tui): run slow commands off the UI thread**

- `prepare_backend_switch` does the slow part without touching state: settings, provider SDK imports and client construction. `apply_backend_switch` commits the result on the UI thread. If `/backend` is used twice quickly, a sequence number makes the newer request win.
- `/codex status` and `/codex account` run through `_run_blocking_command`.
- `apply_thinking_value(persist=False)` uses the debounced saver.
- Attachments read at most `MAX_ATTACHMENT_CHARS` (16,000) characters per file instead of reading the whole file and truncating it.
- Tests: `tests/test_tui_attachments.py`, 3 tests, and a pilot test for the backend switch covering both thread use and the newer request winning.

### Phase 4. Done

**`9c00a26` perf(tui): faster first paint for resumed sessions**

- Finished turns render as one `MarkdownBlock` widget. The per-paragraph split is used only while a turn streams.
- The window is now 12 turns, with pages of 20.
- The input is focused with `call_after_refresh` instead of a 0.5 s timer.
- The codebase-memory status check starts after the first paint.
- Measured with `run_test` at 120x40: an 80-turn session went from about 490 ms to about 240 ms to first frame. An empty session stays around 80 ms.

**`c33f732` perf(cli): import subcommand implementations lazily**

- Each handler in `main.py` imports what it needs. `mtp.cli.main` went from about 340 ms to about 180 ms. `mtp providers list`, `mtp doctor`, `mtp codebase status`, `mtp tui --help` and `mtp new --help` were smoke-tested.

**`51add2d` perf: import mtp's public names on first use** (decision 6)

- `mtp/__init__.py` maps each public name to its submodule and resolves it in `__getattr__`, caching it in module globals.
  - Unknown names fall back to importing a submodule of that name, so `mtp.providers` keeps working.
  - `WebSocketTransportServer` and `run_ws_transport` resolve to `None` if their dependency is missing, as before.
  - `__dir__` lists all exports, and a `TYPE_CHECKING` block keeps IDE completion working.
- The `Agent.MTPAgent`, `Agent.ToolRegistry` and other convenience aliases moved from `__init__` to lazy descriptors at the bottom of `agent.py`.
  - Each resolves on first access and replaces itself, as a `staticmethod` for functions.
  - This keeps `from mtp.agent import Agent; Agent.MTPAgent` working. That would have broken if the aliases stayed in a lazy `__init__`.
- `tui_state.py` imports `Agent` only for type checking.
- The same commit makes `/details` re-render every open chat, and makes quitting ask every running chat to stop.
- The staged `__init__.py` kept the committed version line `0.1.33`. The owner's uncommitted bump to `0.1.34` stays in the working tree.
- Result: `mtp.cli.main` imports in about 48 ms, and importing the TUI no longer loads `mtp.agent`.
- Tests: `tests/test_lazy_package.py`, 8 tests:
  - `import mtp` does not load `mtp.agent` or `mtp.mcp`
  - importing `JsonSessionStore` stays light
  - every `__all__` name resolves
  - star import works
  - aliases work after a direct `Agent` import
  - function aliases stay static
  - submodule access and `AttributeError` behave as before

### Documentation. Done

**`f271e15` docs(cli): document chats, queueing, steering and Ctrl+X**

- `docs/CLI.md` has new sections on chats and multitasking, sending while a reply runs, stopping and failures, keyboard shortcuts, and settings recovery. The `/new` and `/load` descriptions are updated.

### Summary of new and changed modules

| File | Lines now | Role |
|---|---|---|
| `src/mtp/cli/tui_app.py` | 2467 | App, commands, conversation routing |
| `src/mtp/cli/tui_conversation.py` | 223 | New. `Conversation`, `LiveTurn`, `ConversationView`, `QueuedPrompt` |
| `src/mtp/cli/tui_widgets/chat_log.py` | 796 | Incremental widgets, spinner clock, windowing, status and steer notes |
| `src/mtp/cli/tui_widgets/stream_markdown.py` | 147 | New. Streaming and static Markdown |
| `src/mtp/cli/tui_widgets/queue_bar.py` | 50 | New. Queue bar |
| `src/mtp/cli/tui_live_events.py` | 77 | New. Event batcher |
| `src/mtp/cli/tui_persistence.py` | 93 | New. Debounced session writer |
| `src/mtp/cli/tui_indexes.py` | 175 | New. Background indexes |
| `src/mtp/cli/tui_shortcuts.py` | 45 | New. Shortcut list |
| `src/mtp/cli/tui_settings.py` | 400 | Cache, atomic save, corrupt backup |
| `src/mtp/cli/tui_workers.py` | 501 | Snapshots, turn status, backend switch split |
| `src/mtp/cli/tui_mtp_backend.py` | 645 | Merge fix, failure capture, steering events |
| `src/mtp/cli/tui_codex_backend.py` | 1261 | Process-tree cancel, live events |
| `src/mtp/agent.py` | 2772 | Steering, history repair, lazy aliases |
| `src/mtp/__init__.py` | 249 | Lazy exports |

New or extended test files, with test counts:

| File | Tests |
|---|---|
| `test_tui_app_lifecycle.py` | 11 |
| `test_tui_multitasking.py` | 12 |
| `test_tui_turn_status.py` | 6 |
| `test_tui_render.py` | 3 |
| `test_tui_stream_markdown.py` | 8 |
| `test_tui_stream_merge.py` | 7 |
| `test_tui_live_events.py` | 5 |
| `test_tui_persistence.py` | 6 |
| `test_tui_indexes.py` | 6 |
| `test_tui_settings_cache.py` | 8 |
| `test_tui_attachments.py` | 3 |
| `test_tui_codex_cancel.py` | 3 |
| `test_agent_steering.py` | 5 |
| `test_lazy_package.py` | 8 |

---

## 4. What is remaining

### Implementation update, 2026-09-30

- Fixed the slash-command selection loop. Selecting a final argument executes the command once and clears the input. Multi-step `/codebase memory` selections advance to the next argument. Completed typed arguments no longer reopen the same picker, and unrelated OptionLists are ignored by the command handler. Eight new keyboard pilot cases pass; the fast suite passed 524 tests with 11 deselected.
- Updated Codex integration to discover models through `model/list`, including per-model reasoning levels and defaults. `/codex models` refreshes the catalog. Startup respects the configured Codex model, instead of pinning GPT-5.5. Numeric shortcuts follow the returned catalog; MTP provider completions use that provider's model list.
- `/codex account` and `/codex status` fetch the reported ChatGPT plan and live subscription windows through `account/read` and `account/rateLimits/read`. Missing limits remain unknown. Local profile data is labelled when live reads fail. Tokens and raw authentication errors are not displayed.
- Fixed the Codex reasoning override to use `model_reasoning_effort`, including supported `max` and `ultra` values. Thinking controls use the selected model's capabilities. Codex text delta and completed-message events now reach the live chat renderer without duplicating the completed answer.
- Upgraded the local npm Codex CLI from 0.128.0 to 0.159.2. Live metadata returned GPT-6.1 Sol and the current GPT-6 model family, the account plan and rate limits. Fresh and resumed GPT-6.1 Sol turns passed against the real CLI, with live text delivery. Sixteen metadata tests cover protocol handshake, timeout cleanup, catalog parsing, offline fallback, supported effort validation, safe account rendering and text delivery. Further keyboard cases cover first-press Enter and Tab selection, `/thinking`, current models, mixed-case codebase arguments and `/sessions` selection. The fast suite passed 546 tests with 11 deselected.

- Completed 4.1.1. MTP agents restore recent user/reply pairs from the transcript before their first request, including agents prepared by `/backend`. Existing agent history is kept. Restoration keeps at most 40 whole turns, respects the agent's message limit, and reserves half the estimated model context for tools and output. System instructions and the current prompt count against the history budget. Old tool calls and reasoning are omitted; failed and cancelled replies keep their status note.
- Added six tests in `tests/test_tui_history_seed.py`, including the real `/model` command, provider switching, loaded transcripts, duplicate prevention and small-context limits. The targeted suite passed 12 tests. The first full fast-suite run passed 492 tests and failed the existing session-load pilot test; that test passed on an isolated rerun.
- Completed 4.1.2. Both TUI and Agent OS import `merge_stream_text` from `mtp.streaming`. Repeated tokens, newlines and overlapping suffixes are preserved; long full-payload replays retain the existing behavior. Added 14 shared merge cases and five Agent OS integration checks. The stream tests passed 26 tests, and the full fast suite passed 512 tests with 11 deselected.
- Completed 4.1.8. Codebase scans capture their worker, initiating conversation and starting cwd. Completion updates and saves that conversation only if it remains open at the starting cwd. Superseded workers cannot clear a newer scan's state. The explicit scan-root behavior is preserved for the initiating chat. Four new pilot cases cover switching chats, closing the owner, changing its cwd and replacing a scan. The final fast suite passed 516 tests with 11 deselected.
- MTP-provider and real-terminal smoke checks remain outstanding. Codex metadata, fresh turns and resume have been checked against the real CLI. Session storage, restored chats, concurrency limits and the command registry remain planned work.

The detailed entries below retain the original problem descriptions. Completed entries are marked in their headings.

The list is ordered by how much each item affects users. Each item says what is wrong or missing, why it matters, where the code is, how to fix it, and how to test it.

### 4.1 Important gaps in finished phases

#### 4.1.1 MTP chats lose their memory when the agent is rebuilt. Completed 2026-09-30

- **What happens.** For MTP backends, the conversation history lives only in `state.agent.messages`. Many commands set `state.agent = None`:
  - `/model`, `/mode`, `/cd`, `/autoresearch`, `/research`, and `/thinking` on Xiaomi
  - a finished codebase scan
  - `/apikey set` or `delete` for the active provider
  - any backend switch, where `apply_backend_switch` puts a brand new agent in place

  `_run_mtp` then builds a fresh agent with an empty history, so the model forgets everything said earlier in the chat. The transcript on screen still shows it, which makes this confusing. Codex chats are not affected because they resume a Codex session or inject the last five turns.
- **Why it matters.** Changing the model mid-conversation is common, and users expect the conversation to continue.
- **Where.** `tui_workers._run_mtp`, which builds the agent; `tui_harness_agent.build_harness_agent`; every `state.agent = None` in `tui_app.py`; `apply_backend_switch`.
- **Fix.**
  1. Add `seed_history(agent, transcript)` in `tui_workers.py`. It appends `{"role": "user", "content": turn.prompt}` and `{"role": "assistant", "content": turn.history_reply()}` for the recent turns, after the system messages are seeded. Tool calls are left out, because tool results from an old provider would not match the new one's format.
  2. Call it whenever a fresh agent is built for a state that already has a transcript.
  3. Cap it by message count, or better by estimated tokens using the context window table in `tui_model_context.py`, to avoid overflowing small local models.
  4. Remember which provider built the agent, so history is not reseeded into an agent that already has it.
- **Tests.** A fake provider that records `messages`. Run turn one, switch the model, run turn two, and check that turn two's request contains turn one's prompt and reply.

#### 4.1.2 Agent OS still has the token-dropping merge bug. Completed 2026-09-30

- **What happens.** `src/mtp/agent_os/app.py` has its own copy of `_merge_stream_text` with the old "drop if already present" logic, around lines 482 to 492 at the baseline. The Streamlit Agent OS app therefore still loses repeated tokens.
- **Fix.** Move the corrected merge into one shared module, for example `mtp/streaming.py`, and import it in both places.
- **Tests.** Reuse the cases from `tests/test_tui_stream_merge.py` against the shared function.

#### 4.1.3 The whole `sessions.json` is rewritten on every save

- **What happens.** `JsonSessionStore` keeps every session in one JSON file. Each save reads the file, replaces one record and writes all of it back. Because the transcript is stored inside `metadata.tui`, the file grows with every turn of every session. Saves are now off the UI thread and debounced, but each one still costs time proportional to the total history. With several chats saving in parallel, the single writer queues the work up.
- **Fix, in steps.**
  1. Add a `JsonDirSessionStore` that stores one file per session, `<db>/sessions/<session_id>.json`, plus a small `index.json` with id, label, backend, turn count, updated time and cwd for listing.
  2. Migrate on first run by reading the old `sessions.json` once and writing per-session files. Keep the old file as `sessions.json.migrated-<timestamp>` so rolling back to an older version is possible.
  3. Point `load_session_summaries` and `_list_saved_sessions` at the index.
  4. Keep the `SessionStore` protocol unchanged so the Postgres and MySQL stores are not affected.
  5. An alternative is a SQLite store with WAL mode. It needs a schema and migration code, but it gives cheap queries for `/sessions`.
- **Risk.** Old MTP versions reading the same directory would not see new sessions. Document it, and keep writing a compatibility `sessions.json` for one release if needed.
- **Tests.** Migration round-trip, concurrent saves from two chats, listing from the index, crash safety with a temp file and replace per session file.

#### 4.1.4 Blocking work still on the UI thread

These still run on the UI thread. Each is rare, but it freezes the screen while it runs.

- `_request_background_memory_refresh` calls `CodebaseMemory(cwd).status()`, a SQLite open and query, after startup and after every turn. Move the status check into the memory worker and decide scan versus refresh there.
- `/status` and `/codebase status|show` query SQLite directly. Run them through `_run_blocking_command`.
- `/load` resolves the record with `JsonSessionStore.get_session`, which reads the whole file. This becomes cheap once 4.1.3 is done; until then, use `_run_blocking_command`.
- `/sessions`, `/open` and `/history` builders call `_list_saved_sessions`, which parses all of `sessions.json`. Use the session index.
- `tui.py _load_session_hierarchical` calls `save_tui_session` synchronously. The app no longer uses it for `/load`, but `run_tui` and the not-found message path still do.
- `/apikey set|delete` writes settings synchronously. It is a small atomic write and acceptable, but it could use the saver.
- `/codex login|logout|doctor` use `App.suspend()` and a blocking subprocess. This is intended, because those commands take over the terminal, but the preflight `run_codex_login_status` before login could run on a worker first.
- `/codex repair-config` edits a TOML file synchronously. Acceptable.
- `collect_prompt_attachments` still stats and opens up to 8 files on the UI thread, reading at most 16k characters each. To move it off completely, resolve paths on the UI thread for display and do the reads inside `_run_llm_worker` before calling the backend.

#### 4.1.5 Codex text is not streamed. Completed 2026-09-30 for available CLI events

- **What happens.** `_emit_codex_live_line` sees `response.output_text.delta` events but emits only a one-time status message ("assistant is drafting the response"). The reply text appears only when the process exits.
- **Fix.** Emit `("text", delta)` for delta events. Take the delta from the event payload, which needs checking against real Codex JSON output. At the end, reconcile with `--output-last-message`, the same way MTP runs reconcile `final_text`.
- **Tests.** Feed recorded Codex JSON lines through `_emit_codex_live_line` and check the text events.

#### 4.1.6 Queue and open chats do not survive a restart

- **What happens.** Queued messages live only in memory, and `on_unmount` clears them on purpose so they do not start a run while the app exits. Open chats are not restored: the next launch opens only the session given by `--session-id`, or a new one.
- **Fix.**
  1. Save `open_chats: [session_id, ...]`, `active_chat` and each chat's queue into a small `tui_workspace.json` next to the settings file. Write it on every open, close and switch, and on exit.
  2. On launch without `--session-id`, offer to restore: a one-line notice "Restore 3 chats from last time? Enter / Esc".
  3. Restored queues must not auto-run. Show them paused, and let the user press Enter on the queue bar to resume.
- **Tests.** Open two chats and queue a message, close the app, relaunch, and check that both chats return and the queue is shown but paused.

#### 4.1.7 No limit on concurrent runs

- **What happens.** Any number of chats can run at once. Each uses a thread, provider rate limits, and possibly a Codex process.
- **Fix.** Add `max_parallel_runs`, default 3, configurable. `_start_run` checks how many chats are running, and if the limit is reached it queues the message with the note "Waiting for a free slot (3 chats running)". When any run finishes, start the oldest waiting message across all chats.
- **Tests.** Four chats with a limit of 2: two run and two wait, and they start in order as slots free up.

#### 4.1.8 Codebase scan results apply to the wrong chat. Completed 2026-09-30

- **What happens.** When a scan finishes, the handler sets `self._state.cwd = result.root` and `self._state.agent = None`. That is whichever chat is on screen at that moment, not the chat that started the scan. The agent reset also wipes MTP history; see 4.1.1.
- **Fix.** Store the conversation that started the scan in `_start_codebase_scan`, and apply the result to that conversation only. Question whether a scan should change the cwd at all; the old code did it because the scan root could be given as an argument.
- **Tests.** Start a scan in chat 1, switch to chat 2, finish the scan, and check that chat 2 is unchanged.

#### 4.1.9 Memory refresh follows only the active chat

- `_request_background_memory_refresh` uses the active chat's cwd. A background chat that edits files in another folder never triggers a refresh for that folder. Key refreshes by cwd and queue one per distinct cwd.

#### 4.1.10 Smaller issues found during the refactor

- `/clear` shows the boot screen again even though tabs and other chats exist. It should clear the view only, or show a small empty-state message.
- `WorkspaceTree` is created with `Path.cwd()`, which is the process cwd, and re-lists when the chat's cwd differs. Create it with the active chat's cwd.
- The tab label for a running chat is a static `⋯`, because an animated one caused the Textual problem described in 3. Any future animation must update a separate widget, not `Tab.label`.
- The first `Tab` label is built in `compose` before any state is known. It is refreshed on mount; check that it shows the session label for resumed sessions.
- `_fresh_state` copies the active chat's `codex_bin`, cwd, mode and sandbox. That is intended; document it in `/help`.
- `QueuedPrompt` ids are 8 hex characters. Collisions are practically impossible, but ids are only unique per process.
- Steering notes are saved in `assistant_blocks` as `{"type": "steer"}`. Older app versions ignore unknown block types, which is safe, but confirm Agent OS does the same.

### 4.2 Phase 5, shortcuts and commands. Not started except the shortcut list

#### 4.2.1 One command registry

- **Problem.** Commands are still defined in five places: `SLASH_COMMANDS` and `SLASH_WITH_ARG` in `tui_commands.py`, `command_heads` in `parse_slash_command`, the `cmd_desc` dict in `MTPApp._show_command_suggestions`, the palette `COMMANDS` list, and the `/help` table in `_build_help_text`. They drift. `/nerdfont` and `/nf` are documented but do not exist, `/providers` is gone, and `/reset` was missing from the parse sets until this work.
- **Plan.**
  1. Create `tui_command_registry.py` with a `CommandSpec` dataclass: `name`, `aliases`, `summary`, `usage`, `args` (an argument completer callable), `category`, `palette_entries` (a list of preset arguments with labels), `handler_name`, `available_while_running` (bool) and `shortcut` (optional).
  2. Generate the parser sets, suggestions, palette provider and `/help` table from it.
  3. Replace the long if/elif chain in `_dispatch_command` with a dispatch table from name to bound method. Move the argument completers now in `_show_command_argument_suggestions` into the specs.
  4. Add a docs test that parses the command list in `docs/CLI.md` and compares it with the registry.
- **Tests.** Each registered command parses, appears in suggestions and in `/help`, and dispatches. An unknown command still gives "Unknown command".

#### 4.2.2 More key bindings

| Key | Action | Notes |
|---|---|---|
| `PageUp` / `PageDown` | Scroll the chat log by a page | Priority binding. The input is multi-line, so plain Up/Down stay for history. |
| `Ctrl+Home` / `Ctrl+End` | Top / bottom of the chat | End also re-enables auto-follow. See 4.4.1. |
| `Ctrl+Up` / `Ctrl+Down` | Previous / next message | Moves a highlight between message widgets. Needed for message actions in 4.2.5. |
| `Ctrl+R` | History search | Fuzzy list over saved prompt history. See 4.2.3. |
| `Ctrl+E` | Edit the prompt in `$EDITOR` | Use `App.suspend()`, write a temp file, read it back, then delete it. |
| `Ctrl+T` | Toggle all thinking blocks | In the active chat. |
| `F1` or `?` on an empty input | Help overlay | A modal listing `SHORTCUTS` and the commands. |
| `Ctrl+W` | Currently cycles the sandbox mode | Many users expect it to close a tab. Consider moving the sandbox to `/sandbox` only and using `Ctrl+W` for `/close`. This needs the owner's decision. |

Terminal notes to document:

- Windows Terminal passes Alt+digit.
- macOS Terminal needs "Use Option as Meta key".
- Some terminals capture `Ctrl+PageUp` and `Ctrl+PageDown`.
- tmux needs `set -g xterm-keys on`.

#### 4.2.3 Prompt history that lasts

- History is in memory only (`_input_history`) and shared by all chats.
- Store it per project in `~/.mtp/history/<hash of cwd>.jsonl`, append-only, capped at about 2,000 entries, loaded on a worker at startup.
- Deduplicate consecutive repeats.
- Up/Down at the input edges keep working. `Ctrl+R` opens the search list.

#### 4.2.4 Backend-aware model completion

- `/model` suggestions and `/models` list only the Codex presets, even when another backend is active.
- Use `get_provider_models(settings, backend)` for MTP providers.
- For ollama and lmstudio, use `tui_local_providers.discover_models` on a worker, because it makes a network call, and cache the result per base URL.

#### 4.2.5 Message actions

Actions on a highlighted message, or through a click menu:

- copy the reply
- copy one code block, numbering blocks when there are several
- retry the prompt in the same chat
- edit and resend, which puts the prompt back in the input
- fork a new chat from this point, copying the transcript up to it

Retry and fork need a way to cut the transcript and, for MTP, reseed the agent (4.1.1).

### 4.3 Phase 6, multitasking. Mostly done; remaining parts

- **Background tasks panel.** A modal or sidebar section listing running chats, with elapsed time and the current step, plus codebase scans with percent. Each row has a cancel action. It reads from `Conversation` objects and the scan state; no new state is needed.
- **Live run metrics.**
  - The status bar shows usage only after a turn ends.
  - MTP `llm_response` events include usage per round. Send them to the UI as a `usage` live event and show them while the run is going.
  - Show running tokens, tokens per second, elapsed time and a context bar that uses `format_context_usage`.
  - For Codex, parse the `usage` lines Codex emits during the run.
- **Run-state colors.** Color the status bar and the input border by state: idle, running, stopping, failed. Keep the colors in theme variables; see 4.4.7.
- **Per-chat notifications setting.** Allow muting background toasts, for example with `/notify off`.
- Also remaining in this phase: the concurrency limit (4.1.7) and restoring chats after a restart (4.1.6).

### 4.4 Phase 7, motion and visual polish. Not started

Every item must respect a reduced-motion switch: `/motion off`, the environment variables `MTP_REDUCED_MOTION=1` and `NO_COLOR`, and Textual's `App.animation_level`. Animations should use Textual's `styles.animate` or its animator, never extra timers, and must not relabel `Tabs`.

1. **Follow the bottom only when you are there.**
   - Today every append calls `scroll_end(animate=False)`, which yanks the view down while you read older messages.
   - Track "at bottom" with `ChatLog.is_vertical_scroll_end`. Follow only when it is true.
   - Otherwise show a small "↓ new messages" pill above the input; clicking it or pressing `Ctrl+End` jumps down.
   - Use `scroll_end(animate=True, duration=0.15)` when following. At 10 renders a second this must not queue animations; skip starting one if one is already running.
2. **Collapse and expand.** Animate the height of thinking and tool bodies over about 150 ms instead of switching `display`. Textual can animate `styles.height` from a measured height to 0. Or animate opacity and set display at the end, which is cheaper and avoids layout jumps.
3. **Fade in fresh text.** In `StreamingMarkdown`, render the tail in a slightly brighter style and move it back to normal over 2 or 3 frames when a segment freezes. This is the cheap version of the "phosphor" effect the docs already claim. Apply it only to the live tail, never to history.
4. **Input feedback.** Pulse the input border once on submit. Color the border by the active chat's state. On queue, briefly highlight the queue bar row.
5. **Toasts.** Toasts already exist for background completions, failures and settings recovery. Add them for scan completion. Keep all toasts short and give failures a longer timeout.
6. **Boot screen.**
   - `BootLogo` renders about 24 gradient frames by rebuilding a `Text` per character.
   - Precompute the frames once, or cut the sweep to 1 s.
   - Skip the boot screen entirely when resuming a session with turns.
7. **Themes.**
   - `chat_log.py`, `queue_bar.py`, `status_bar.py` and `sidebar.py` hardcode hex colors.
   - Move them to CSS variables in `tui_app.tcss` (`$user`, `$agent`, `$tool`, `$warn`, `$error`, `$muted`) and Rich styles read from one theme table.
   - Add `/theme <name>` with at least a dark and a light theme, saved in settings.
8. **Diffs for edit tools.**
   - `edit.apply_patch` results are rendered as fenced diff Markdown.
   - Render them with `rich.syntax.Syntax("diff")`, with added and removed line colors.
   - On wide terminals (120 columns or more), offer a side-by-side view.
   - Keep the 12,000-character cap.
9. **Nerd Font setting.** `/nerdfont` and `/nf` are documented but missing. Either add them, as a setting in `tui_settings` read by `tui_theme`, or remove them from the docs.

### 4.5 Phase 8, onboarding and cleanup. Not started

1. **Provider setup modal.**
   - `/backend <name>` on an unconfigured provider currently prints "Set API key first with /apikey set".
   - Replace that with a modal: a masked API key field, a base URL for local or hybrid providers, a model picker filled from `get_provider_models` or local discovery, and a "Test" button that sends a one-token request on a worker.
   - Save on success, then switch.
   - `tui_local_setup.py` is the old ANSI version of this and should be deleted afterwards.
2. **Mask keys.** `/apikey show <provider>` prints the full key into the command log, and the terminal scrollback may keep it. Mask it by default, and require `/apikey show <provider> --reveal` plus a confirmation.
3. **Split `tui_app.py`**, currently 2467 lines, into:
   - `tui_commands/` with one module per command group: sessions, models, codex, codebase, apikey, chats
   - `tui_runs.py` for start, finish, live events, queue and steering
   - `tui_completion.py` for suggestions
   - `tui_views.py` for the Rich text builders: help, status, history, models, tools, sessions

   Do this after the command registry (4.2.1) so the dispatch table is the seam.
4. **Delete dead code and duplicates.**
   - `tui_local_setup.py`, `tui_toast.py`, the ANSI parts of `tui_theme.py` other than `SYM_OK` and `SYM_ERR`, and the synchronous `switch_backend` wrapper once nothing calls it.
   - `_extract_tool_events_from_agent` and `_extract_usage_metrics`, which are TODO stubs.
   - The duplicate `_format_tool_detail_line` in `tui_app.py` and `chat_log.py`.
   - The duplicate session listing in `tui.py` and `tui_app.py`.
   - The `CmdLogProxy` class rebuilt on every `_dispatch_command` call; replace it with `_write_cmd_log`.
   - Scratch files in the repo root such as `calculator.py`, `hello.html` and `js_script.js`, if they are not needed.
5. **Stale documents.** The root-level `*_FIX.md`, `IMPLEMENTATION_*.md` and similar files cite line numbers in a `tui.py` that no longer exists. Move them to `docs/archive/` or delete them.
6. **Fix documentation drift in `docs/CLI.md`.**
   - The "Modern UI/UX & Aesthetics" section describes a cat companion, a phosphor effect, input pulses and a HUD that do not exist. Rewrite it to match what exists, or build the parts from 4.4 first.
   - The provider default model list is spliced into the middle of the metrics section.
   - `/mode`, `/sandbox`, `/cd`, `/tools`, `/details` and `/compose` are missing from the command list.
7. **Pin Textual.** `pyproject.toml` still says `textual>=1.0.0`. The code now relies on Textual 8 behavior:
   - the `Tabs` API
   - thread-safe `post_message`
   - `is_mounted` timing
   - `ContentSwitcher`
   - `check_action` fallthrough
   - `Content`-based tab labels

   Pin `textual>=8.2,<9` and test the lower bound in CI.
8. **Repository hygiene.**
   - `tests/conftest.py` and several test files are untracked, including `test_tui_codex_auth_commands.py`, `test_runtime.py` and `test_tools.py`, so CI does not run them. Decide whether to commit them.
   - The owner's `0.1.34` version bump in `pyproject.toml` and `src/mtp/__init__.py` is uncommitted. Commit both together so the version-check test stays green.

### 4.6 Measurements still needed

- The startup numbers so far come from `run_test` and import timing. Measure a real launch too: time from process start to the first frame in Windows Terminal and one POSIX terminal, with an empty session and an 80-turn session. One way is `MTP_TUI_DEBUG=1` printing `time.perf_counter()` deltas at `main()`, at `run_tui`, and at the first `on_mount` and `call_after_refresh`.
- Frame cost while streaming a long reply, around 20k characters with code blocks: log the duration of `set_live_assistant_message` behind the debug flag, and check that it stays flat as the reply grows.
- Memory use with 5 chats of 100 turns each, making sure unmounted history does not keep widgets alive.
- Parallel streaming with 3 real providers at once: check that the UI stays responsive, with keystroke-to-render latency under about 50 ms.

---

## 5. How to verify the remaining work

**Automated.**

- Run the fast suite after every change: `python -m pytest -m "not integration and not live" --basetemp=<temp dir>`.
- Add pilot tests next to the existing ones and reuse `FakeRunner`.
- For any new widget, add a layout test like `test_chat_keeps_most_of_the_screen_while_running_with_a_queue`, which checks regions. A headless screenshot (`app.save_screenshot`) rendered to PNG caught two layout bugs that the unit tests missed.

**Manual smoke test against real backends.** None of the work so far has been run against a live provider.

1. **Codex:**
   - log in and send a prompt
   - press `Ctrl+X` mid-run and check that no `node` process is left in Task Manager
   - send a follow-up and check that it resumes the session
   - queue a message during a run and check that it runs next
2. **One MTP cloud provider**, for example groq:
   - a tool-using prompt, then steer with `Ctrl+G` during the tool phase
   - check the "↳ You, mid-run" note and that the next round uses it
3. **One local provider** (ollama or lmstudio): streaming text and thinking blocks, then `Ctrl+X`.
4. **Two chats at once** on different backends: switch back and forth while both stream, and check the unread markers and toasts.
5. **Break the network mid-run:**
   - check that the turn shows "Run failed" with the partial text
   - then send a message and check that the chat continues
   - for MTP, check that history repair lets the next request through
6. **Corrupt the settings file on purpose** and launch: check the warning toast, the `.corrupt-` copy, and that the keys can be recovered from it.
7. **Quit while two chats are running:** check that the app exits promptly and that both sessions are saved.

---

## 6. Decisions already made by the project owner

These were answered during this work and should not be revisited without asking.

1. **Sending during a run.** The message is queued and runs next. When the backend supports it, the user can also steer it into the running reply. Both are offered.
2. **Interrupt key.** `Ctrl+X`, not `Ctrl+C`, because `Ctrl+C` is copy.
3. **Failed and cancelled turns.** Save them as they are, and let the user keep sending messages in that chat.
4. **Corrupt settings.** Back up the file and warn.
5. **State commands during a run.** Real multitasking: several chats in parallel, each streaming normally, and the user can switch to any of them. Changing a running chat's settings applies to its next message.
6. **Lazy `import mtp`.** Left to the implementer's judgment, on the condition that everything keeps working. It was done with lazy exports plus lazy `Agent` aliases, covered by `tests/test_lazy_package.py`.

Open questions that still need the owner:

- Should `Ctrl+W` close a tab instead of cycling the sandbox mode? See 4.2.2.
- Session storage: one file per session or SQLite? See 4.1.3.
- Should a finished codebase scan change the chat's working directory at all? See 4.1.8.
- Restore open chats automatically, or ask first? See 4.1.6.
- Default limit on parallel runs: 3? See 4.1.7.
