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
