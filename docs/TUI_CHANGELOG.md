# TUI release notes

This file records the major TUI milestones. For the current command reference,
use [`CLI.md`](CLI.md) or `/help` inside the application.

## Current development release

- Supports Codex plus the cloud and local MTP SDK backends listed in
  [`CLI.md`](CLI.md).
- Stores sessions centrally in `~/.mtp/sessions/` by default and provides list,
  load, read-only open, history, labels, and generated first-prompt titles.
- Stores cloud API keys in the operating system credential vault. Masked entry
  keeps keys out of history, autocomplete, transcripts, and undo/redo state.
- Streams text and tool activity incrementally while bounding retained render
  state, transcripts, attachments, workspace suggestions, and tool details.
- Adds a searchable command palette, workspace sidebar, provider/model controls,
  codebase memory, thinking controls when supported, and usage metrics when
  reported by the backend.
- Distinguishes the Codex sandbox from MTP application-level permission
  profiles. MTP `workspace-write` confines harness file operations and working
  directories to the workspace and rejects unsafe shell patterns.
- Cancels Codex and MTP harness subprocess trees when a turn is cancelled.

## April 2026

- Introduced the Textual application, multi-provider switching, local Ollama and
  LM Studio setup, persistent sessions, streamed tool events, attachments, and
  the initial telemetry sidebar.

The TUI evolves with the SDK. Historical command prototypes such as
`/model add`, `/models refresh`, `/provider`, `/api-key`, and `/nerdfont` are not
current commands; use `/model <model-id>`, `/backend`, `/apikey`, and `/help`.
