# Provider implementation phase 2

Completed on 2026-10-05. Version 0.1.39 is prepared locally and in GitHub;
PyPI publication is pending.

## Delivered

- A reusable OpenAI-compatible Chat Completions adapter with native text/tool
  streaming, reversible tool-name mapping, reasoning-content replay, usage
  extraction, and per-client credentials.
- Hugging Face, DeepInfra, and DashScope adapters with SDK aliases, optional
  installation extras, CLI registration, doctor checks, TUI setup, and model
  catalogs. Hugging Face catalogs preserve named live routes. DashScope accepts
  regional/workspace endpoints and enforces streaming for enabled thinking.
- An opt-in OpenAI Responses adapter with `store=False`, encrypted reasoning
  replay, native function-call IDs, and session persistence. Existing OpenAI
  callers retain Chat Completions.
- Endpoint and output-budget fields in the four new TUI setup forms. Successful
  saves clear the key input; credentials stay outside conversation history.
- Incomplete streams and malformed or duplicate call IDs fail before tool
  execution. Identical reads retain separate dependency and approval checks.
  Explicit TTL caching remains available after those checks.
- Provider guides, configuration reference, runnable example, and refreshed
  model defaults in the documentation indexes.
- Cross-platform shell executable-path rejection before parsing. TUI tests wait
  for an observable mounted/focused form rather than a fixed startup delay.

## Verification

| Check | Result |
|---|---|
| Complete non-live suite after CI fixes, all installed provider SDKs | 804 passed, 1 skipped, 11 live tests deselected |
| Fast suite in CI with provider SDKs absent after fixes | 759 passed, 6 optional-dependency skips, 51 deselected |
| New provider/setup regressions | 56 passed |
| Actual installed OpenAI SDK on loopback JSON/SSE servers | 16 passed |
| Isolated installed 0.1.39 wheel on the same wire contracts | 16 passed |
| Live compatible adapter against authorized Groq | Parallel, sequential references, and mixed batches passed |
| TUI setup with 40x15, 80x24, and 120x40 terminals | Save/settings/key-clearing passed for all four new backends |
| Docs checks, focused Ruff, byte compilation, dependency checks | Passed |
| Wheel and source distribution | Built successfully |
| CI follow-up, shell and TUI regressions | 117 passed |
| GitHub implementation matrix | Five jobs passed across Linux, Windows, and macOS |

The full suite's skip is the optional Streamlit UI, which is not installed.
The fast suite also skips four real OpenAI constructor checks and one Gemini
SDK-type check. The full environment runs those checks. Cohere emitted one
upstream Pydantic deprecation warning.

The fast suite tests source with minimal dependencies. The separate isolated
wheel check imports `mtp` from the clean environment's `site-packages` and runs
copied wire tests without the repository's source-path fixture.

Groq tests use `qwen/qwen3.8-27b` with a 512-token output budget and synthetic
calculator tools. [Recorded results](audits/provider-phase2/groq-compatible.json)
contain outputs and batch modes, without keys or request headers.

## TUI browser checks

The actual TUI ran through Textual Serve on loopback at `http://127.0.0.1:8768`.
In-app browser screenshots confirmed the DashScope endpoint, budget, masked key,
and setup actions. Submitting an empty key displayed validation feedback.
Browser-control typing did not reliably reach the terminal. A separate native
Zen browser check used the computer-use tool to click Cancel and verified that
the empty composer returned. No real key was entered or inference started.

The Textual test runner verified Escape cancellation, field persistence, key
clearing, and empty history. Rendered evidence is saved at
[40x15](audits/provider-phase2/dashscope-40x15.svg),
[80x24](audits/provider-phase2/dashscope-80x24.svg), and
[120x40](audits/provider-phase2/dashscope-120x40.svg).
The [browser screenshot](audits/provider-phase2/dashscope-browser.jpg) shows the
live form and empty-key validation feedback.

## Verification limits and next work

Live inference used only the authorized Groq key. OpenAI Responses, Hugging Face,
DeepInfra, and Alibaba passed installed-SDK serialization and streaming fixtures;
their live account behavior remains unverified. Hugging Face and DeepInfra
public catalogs were read to check model data. Their official contracts and
endpoint guidance are linked in each provider guide.

The new adapters default to text input. Native async SDK clients, hosted
Responses tools, automatic server conversation IDs, provider-enforced output
schemas, and multimodal certification are outside this phase. Custom models
can reject options that their endpoint does not support.

Modern MCP Streamable HTTP/revision support and operating-system sandboxing are
separate future phases. The earlier audit's legacy MCP support declaration and
application-level execution policy remain in force.

GitHub's first phase-two matrix found a POSIX path-validation diagnostic mismatch
and Windows TUI startup timing assumptions that local Python 3.13 did not expose.
Both received fixes and focused regressions. The [final implementation matrix](https://github.com/GodBoii/Model-Tool-protocol-/actions/runs/37250532071)
passed all five jobs for commit `d842522`: fast tests on Linux, Windows, and
macOS, plus installed-provider integration tests on Linux and Windows. The same
commit passed [docs consistency](https://github.com/GodBoii/Model-Tool-protocol-/actions/runs/37250532054)
and [MCP smoke](https://github.com/GodBoii/Model-Tool-protocol-/actions/runs/37250532026).
The remaining report/evidence commit changes documentation only.
