# Provider boundary hardening

Completed on 2026-10-05 for the compatible, Hugging Face, DeepInfra,
DashScope, and OpenAI Responses adapters.

The boundary review found settings that either bypassed an adapter's declared
contract or failed after SDK client construction. The changes reject these
settings before inference and preserve valid provider requests.

## Changes

- Endpoints reject whitespace, control and format characters, malformed or
  empty ports, port zero, credentials, percent-encoded authorities, query and
  fragment delimiters, and backslashes. HTTPS endpoints and explicit loopback
  HTTP endpoints remain supported. Diagnostics omit the supplied URL.
- Streaming usage and parallel-call flags require actual booleans. Input
  configuration must contain text once and may also contain image once.
  Timeouts require finite positive numbers. Extra request bodies require
  dictionaries and cannot replace owned sampling, stream, or token settings.
- DashScope workspace IDs must be valid DNS labels of at most 63 characters.
  Invalid region types and thinking flags fail during construction.
- Responses accepts text input configuration only. Media attachments and known
  native media blocks raise errors instead of being dropped or serialized as
  prompt text. Callers use `max_output_tokens`; passing `max_tokens` reports the
  correct option. State, reasoning, background, and conversation settings remain
  owned by the stateless adapter.
- Tool calls reject missing function names, unsupported call types, and
  non-finite JSON argument values before execution. Streams reject unsupported
  finish markers, output after completion, and non-string function fragments.
  Early consumer cancellation still closes the SDK stream. Responses also
  rejects unfinished assistant messages inside completed envelopes.

Requests still omit unspecified options. Valid image configuration for the
generic Chat Completions adapter remains available. Responses retains native
call IDs and opaque encrypted reasoning during session replay. No new live
provider inference requests were made for this review.

## Verification

`tests/test_phase3_provider_boundaries.py` covers rejected endpoint/configuration
inputs, supported local endpoints, owned request settings, media rejection,
malformed streams, cancellation, and native reasoning replay. The preceding
phase's provider/TUI contracts and 16 actual installed OpenAI SDK JSON/SSE
round trips remain regression coverage. Focused Ruff checks pass.

The focused run passed all 160 tests: 88 new boundary tests, 56 preceding
provider and TUI contracts, and 16 installed-SDK wire tests. Byte compilation
and Ruff format checks passed for the edited Python files.

These checks validate MTP's request and execution boundaries. They do not
certify live account access, model-specific parameter support, or multimodal
behavior across hosted providers. New adapters continue to use the synchronous
SDK with MTP's async thread bridge.

The official contracts reviewed were
[OpenAI Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses),
[Alibaba function calling](https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling),
[Hugging Face gpt-oss usage](https://huggingface.co/docs/inference-providers/en/guides/gpt-oss),
and [DeepInfra tool calling](https://docs.deepinfra.com/chat/tool-calling).
The endpoint and configuration restrictions above describe MTP's implementation;
they do not imply additional requirements imposed by those providers.
