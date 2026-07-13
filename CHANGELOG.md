# Changelog

All notable changes to MTPX are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Native asynchronous request and finalization paths across the supported cloud
  and local provider adapters, with streamed planning/tool-call assembly for
  OpenAI, Groq, Xiaomi, and compatible paths.
- Incremental final-response streaming for Anthropic, Gemini, Cohere, Mistral,
  OpenRouter, Together, Fireworks, Cerebras, DeepSeek, SambaNova, Ollama, and LM
  Studio where their SDKs expose streaming.
- Provider capability metadata, normalized safe provider errors, structured
  request options, strict tool schemas where supported, and provider-specific
  setup guides.
- CLI provider inspection in text or JSON, including dependency and credential
  readiness without exposing secrets.
- CLI session list, show, delete, export, and import commands with an atomic,
  versioned, validated JSON interchange format.
- TUI codebase memory, tool-detail views, model-aware thinking controls,
  provider credential management, and a searchable command palette.
- A performance benchmark for runtime scheduling, transcript retention, TUI
  streaming, and workspace indexing hot paths.
- CI coverage for every supported Python minor version and a clean built-wheel
  installation/import/CLI smoke test.

### Changed

- Tool execution can run independent calls concurrently with bounded worker
  counts, dependency failure propagation, cooperative timeout cancellation, and
  process-tree termination for subprocess-backed tools.
- Provider adapters prefer native async clients instead of occupying worker
  threads and consistently stream final text through the Agent event API.
- The JSON session store uses atomic durable writes, stale-lock recovery,
  bounded listing, exact owner identity, and safer import semantics.
- Provider credentials are stored in the OS credential vault; existing
  plaintext settings are migrated only after a confirmed vault write.
- TUI streaming updates are incremental, transcript/display state is bounded,
  workspace suggestions are cached and bounded, and large attachment reads are
  capped to reduce CPU and memory growth.
- Model defaults and context metadata for Anthropic and Gemini are centralized
  in the SDK model catalog.
- Package runtime version is derived from installed distribution metadata, so
  it cannot drift from `pyproject.toml`.

### Fixed

- Tool-call reference normalization, malformed JSON tool arguments, dependency
  scheduling, unsafe overlapping Agent runs, and overly broad provider retry
  behavior.
- API keys appearing in interactive history, autocomplete, transcripts, or
  editor state.
- MTP harness permission-profile enforcement, workspace path escape, unsafe
  subprocess patterns, and cancellation of long-running harness commands.
- Unbounded or unsafe local/remote media reads, including private-network URL
  fetches and redirect revalidation.
- Provider response metadata, usage extraction, async reasoning fields, and
  streamed delta assembly across multiple adapters.
- Packaging of hidden scaffold template files and runtime behavior when optional
  provider dependencies are absent.

### Security

- Media URLs reject credentials, non-HTTP schemes, private/link-local/loopback
  destinations, unsafe redirects, oversized responses, and unsupported content
  types by default.
- TUI attachments reject workspace escapes and secret-like files and enforce
  per-file, count, and aggregate byte limits.
- Provider exceptions are normalized at the Agent boundary to avoid returning
  credential-bearing SDK error text.

## [0.1.15] - 2026-04-17

### Added

- Initial Textual TUI layout, telemetry sidebar, streamed tool status, and
  session navigation.

## [0.1.10] - 2026-04-11

### Added

- Provider selection and initial API-key/model configuration in the TUI.

## [0.1.3] - 2026-04-06

### Added

- CLI scaffolding and run commands, optional provider/toolkit/store extras,
  WebSocket transport, and project templates.

## [0.1.0] - 2026-04-06

### Added

- Core protocol entities, multi-round Agent runtime, provider adapters, built-in
  toolkits, session persistence, policy-aware execution, MCP JSON-RPC adapters,
  transports, events, and multimodal payload types.

[Unreleased]: https://github.com/GodBoii/Model-Tool-protocol-/compare/v0.1.15...HEAD
[0.1.15]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.15
[0.1.10]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.10
[0.1.3]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.3
[0.1.0]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.0
