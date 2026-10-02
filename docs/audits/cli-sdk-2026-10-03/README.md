# CLI, SDK, and provider audit

Audit date: 2026-10-03, Asia/Calcutta. Package under review: MTPX 0.1.37.

This audit checks implementation, current official provider documentation, the
existing test suite, additional adversarial protocol cases, live Groq requests,
and the actual Textual TUI through browser and Windows computer-use tools.

The audit is in progress. Results below are preliminary until the evidence files
and final findings are complete. Provider API contracts are checked locally;
only Groq is authorized for live provider requests.

## Baseline

- Existing fast suite on the user's Python 3.13.3 environment: 622 passed,
  11 deselected, 68.25 seconds.
- Existing integration command: no tests selected, 633 deselected. The CI job
  explicitly converts pytest's no-tests exit status to success.
- The initial Git checkout was clean on `main`.
- TUI verification uses a separate session database under `tmp/`.

## Evidence under construction

The final report will include a provider-by-provider documentation matrix,
severity-ranked findings with code locations and repros, protocol execution
evidence, TUI screenshots, and a prioritized list of additional providers.

## Additional protocol checks

`tests/test_audit_regressions.py` exercises all 15 adapters with synthetic native
responses and the actual runtime. Its 34 positive checks pass. Nine strict
expected failures reproduce unresolved defects. Running the same file with
`--runxfail` confirms nine actual failures and 34 passes.

Live Groq batch probes pass on `qwen/qwen3.8-27b`: one parallel batch of three
calls, four sequential batches linked by `$ref`, and a mixed parallel/sequential
graph. Handler timestamps prove overlap for independent calls. The account's
models endpoint does not list `llama-3.3-70b-versatile`, and requests return 404.
`openai/gpt-oss-120b` did not meet the single-response batch requirements.
