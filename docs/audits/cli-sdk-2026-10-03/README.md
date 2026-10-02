# CLI, SDK, and provider audit

The completed audit is in [AUDIT.md](AUDIT.md). The notes below record the early
baseline; use the completed report and evidence summary for final results.

Audit date: 2026-10-03, Asia/Calcutta. Package under review: MTPX 0.1.37.

This audit checks implementation, current official provider documentation, the
existing test suite, additional adversarial protocol cases, live Groq requests,
and the actual Textual TUI through browser and Windows computer-use tools.

The audit is complete for the scope in AUDIT.md. Provider API contracts were
checked locally; only Groq was used for live provider requests. The report
separates completed checks from work that still needs credentials or services.

## Baseline

- Existing fast suite on the user's Python 3.13.3 environment: 622 passed,
  11 deselected, 68.25 seconds.
- Existing integration command: no tests selected, 633 deselected. The CI job
  explicitly converts pytest's no-tests exit status to success.
- The initial Git checkout was clean on `main`.
- TUI verification uses a separate session database under `tmp/`.

## Evidence available

The completed report includes a provider-by-provider documentation matrix,
ranked findings with code locations and repros, protocol execution evidence,
TUI screenshots, and a prioritized list of additional providers.

## Additional protocol checks

The three new audit test files exercise all 15 adapters, real SDK serialization,
and loopback transports. Running them with `--runxfail` produces 18 failures
and 53 passes. The final full non-live suite reports 670 passes, 18 strict
expected failures, and one skipped module. Production defects remain unresolved.

Live Groq batch probes pass on `qwen/qwen3.8-27b`: one parallel batch of three
calls, four sequential batches linked by `$ref`, and a mixed parallel/sequential
graph. Handler timestamps prove overlap for independent calls. The account's
models endpoint does not list `llama-3.3-70b-versatile`, and requests return 404.
`openai/gpt-oss-120b` did not meet the single-response batch requirements.
