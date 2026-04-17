# Changelog

All notable changes to MTPX will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.15] - 2026-04-17

### Added
- **TUI Cat Companion 2.0**:
  - Interactive eye tracking (pupils follow prompt cursor).
  - Telemetry HUD sidebar (CWD, Sandbox, Attachments).
  - Terminal resize stabilization and artifact clearing.
  - Scroll-smear protection during response generation.
- **TUI Micro-animations & Aesthetics**:
  - Phosphor Decay: CRT-style typewriter streaming effect.
  - Dynamic Input Pulse: Physical border-flash feedback on submission.
  - Active Tool Spinners: Live tool status with success/fail markers.
  - Animated Toast Grace: Ease-out cubicglide for notifications.
  - Nerd Font Mode: Native `/nerdfont` toggle command with `.env` persistence.


## [0.1.10] - 2026-04-11

### Added
- TUI provider onboarding flow for `mtp-openai` backend:
  - New provider selection command: `/provider <openai|groq|claude|openrouter|gemini>`.
  - New provider visibility command: `/providers`.
  - New provider key command: `/api-key <key|env|clear>`.
- Local provider settings persistence in `tui_provider_settings.json`.

### Changed
- `mtp tui` now supports `--provider`, `--model`, and `--api-key` for the `mtp-openai` backend.
- `--openai-model` is kept as a legacy OpenAI-only override for compatibility.
- Backend separation is explicit: Codex auth flow (`/codex-login`, `codex` backend) remains unchanged and isolated.
- Package/runtime version updated to `0.1.10`.

## [0.1.8] - 2026-04-10

### Added
- New features and improvements
- Enhanced functionality

### Changed
- Updated dependencies and configurations
- Performance optimizations

### Fixed
- Bug fixes and stability improvements

## [0.1.7] - 2026-04-10

### Added
- New features and improvements
- Enhanced functionality

### Changed
- Updated dependencies and configurations
- Performance optimizations

### Fixed
- Bug fixes and stability improvements

## [0.1.6] - 2026-04-10

### Added
- New features and improvements
- Bug fixes and enhancements

### Changed
- Updated dependencies and configurations
- Performance improvements

## [0.1.5] - 2026-04-06

### Added
- MIT License file with proper copyright attribution

### Changed
- Updated copyright holder to Prajwal Ghadge

## [0.1.4] - 2026-04-06

### Changed
- Updated documentation and package metadata
- Minor improvements and bug fixes

## [0.1.3] - 2026-04-06

### Added
- CLI interface with `mtp` command
- Optional dependencies for better package management:
  - Provider-specific extras (openai, groq, anthropic, etc.)
  - Toolkit extras for web scraping
  - Database store extras (postgres, mysql)
  - Convenience aggregates (providers, all)
- WebSocket transport support
- CLI templates support
- Maintainer field crediting Himesh Mehta

### Changed
- Improved package structure with optional dependencies
- Better dependency management for different use cases
- Updated author information

### Fixed
- Package metadata and contributor attribution

## [0.1.0] - 2026-04-06

### Added
- Initial release of MTPX (Model Tool Protocol Extended)
- Core protocol entities (ToolSpec, ToolCall, ExecutionPlan)
- Agent runtime with multi-round model-tool-model loops
- Support for multiple LLM providers:
  - OpenAI
  - Groq
  - Anthropic
  - Google Gemini
  - OpenRouter
  - SambaNova
  - Cerebras
  - DeepSeek
  - Mistral
  - Cohere
  - Together AI
  - Fireworks AI
- Built-in toolkits:
  - Calculator
  - File operations
  - Python execution
  - Shell commands
  - Website scraping
  - Wikipedia search
- Session persistence with JSON, PostgreSQL, and MySQL backends
- Policy-aware execution based on tool risk levels
- Dependency-aware batch tool execution
- MCP (Model Context Protocol) JSON-RPC adapter
- Transport primitives (stdio + HTTP)
- Event streaming support
- Multimodal support (Audio, Image, Video, File)

### Documentation
- Comprehensive documentation in `/docs` directory
- Quickstart guide
- Provider integration guides
- Tool creation guide
- Architecture documentation
- Publishing guide

[0.1.9]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.9
[0.1.8]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.8
[0.1.7]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.7
[0.1.6]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.6
[0.1.5]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.5
[0.1.4]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.4
[0.1.3]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.3
[0.1.0]: https://github.com/GodBoii/Model-Tool-protocol-/releases/tag/v0.1.0
