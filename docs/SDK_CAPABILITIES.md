# Native SDK capabilities

Phase 7 adds optional native async requests to the OpenAI-compatible hosted
adapters, OpenAI Responses, and Groq. Existing callers retain their synchronous
client or async thread bridge unless they opt in. Capability reports describe the
configured adapter, not all models available on a provider.

## Async clients

Pass `native_async=True` with the provider key to create an `AsyncOpenAI` or
`AsyncGroq` client. Alternatively inject `async_client` to control transport,
credentials, timeouts, or SDK configuration. Injection alone requires no
environment key or synchronous SDK client.

```python
from openai import AsyncOpenAI
from mtp.providers import OpenAIResponses

async def request():
    async with AsyncOpenAI() as client:
        provider = OpenAIResponses(async_client=client, model="gpt-4o")
        action = await provider.anext_action(
            [{"role": "user", "content": "Say hello."}], []
        )
        return action.response_text
```

Native methods include `anext_action`, `astream_next_action`, `afinalize`, and
`afinalize_stream`. Sync and async streams share protocol validation. Calls
execute only after valid terminal events and complete argument parsing.
Cancelling an async read or closing the consumer iterator closes the SDK HTTP
stream. `provider.aclose()` closes clients created by the provider. Callers own
and close injected clients.

Hugging Face, DeepInfra, DashScope, and the generic compatible adapter use
`AsyncOpenAI`; Responses uses its native `responses.create` endpoint. Groq uses
`AsyncGroq`, its token budget and reasoning arguments, and `x_groq` usage. An
async-only provider raises a clear error if used with a synchronous agent API.
Native async describes request I/O. The upstream SDK may collect platform
metadata in a worker thread.

## Explicit media support

Compatible Chat Completions adapters accept
`input_modalities=("text", "image")` when the selected model supports vision.
The default remains text input. Responses now supports explicit image and file
input through the native API:

```python
from mtp.media import File, Image
from mtp.providers import OpenAIResponses

provider = OpenAIResponses(
    model="gpt-4o",
    enable_multimodal=True,
    input_modalities=("text", "image", "file"),
)
messages = [{
    "role": "user",
    "content": "Read the document and describe the image.",
    "images": [Image(url="https://example.com/image.png")],
    "files": [File(filepath="document.pdf")],
}]
```

Image/file objects become native `input_image` and `input_file` blocks. File
IDs, URLs, local paths, and bytes are supported. The adapter does not fetch
remote URLs locally; the provider receives the URL. Local file and image
payloads have a 20 MiB per-item limit. URLs reject credentials, control
characters, and unsupported schemes. Default text mode still rejects media.
Audio, video, generated media, automatic file upload, and hosted Responses
tools are not added by this phase. Use a model and account that support the
chosen input types.

Session serialization preserves media bytes through their base64 `to_dict`
format before generic dataclass handling. This fixes binary corruption during
JSON session replay and in the record format used by PostgreSQL/MySQL. File
text content is also encoded consistently for the existing media reader.

## Structured outputs

Install `pip install "mtpx[structured-output]"` for local JSON Schema validation.
Set `output_schema` on a compatible or Responses adapter to request native
strict JSON Schema output. Chat Completions sends `response_format.json_schema`;
Responses sends `text.format`. Without this option the previous
`client_validated` capability stays unchanged.

```python
provider = OpenAIResponses(output_schema={
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
})
```

The adapter checks the schema with `jsonschema` Draft 2020-12 during
construction. Final JSON text must pass local validation before the adapter
returns a final action. Parsed output appears in action metadata under
`structured_output`. Malformed JSON, non-finite numbers, refusals represented
as non-JSON text, and schema mismatches fail clearly. Local references are
allowed; external `$ref` and `$dynamicRef` URLs are rejected to prevent
validation from fetching remote schemas. The server's strict schema subset can
be narrower than Draft 2020-12, so the selected model may reject some valid
local schemas. Tool schemas retain their separate reference-compatible contract.

## Model hints and verification

`mtp.providers.model_profiles.model_capability_hints(provider, model)` returns
conservative documentation hints for exact known model names. Unknown names
return `None`. Hints do not change the adapter configuration, resolve aliases,
or certify account access. The initial entries cover documented gpt-4o Responses
vision/schema inputs and Groq gpt-oss parallel-call restrictions.

Tests use actual installed AsyncOpenAI/AsyncGroq clients against loopback
JSON/SSE servers. They cover native tool-name replay, finalization, schema/media
serialization, actual HTTP response closure on cancellation and early consumer
exit, and unchanged default text contracts. Deterministic injected-client tests
also cover cancellation before a stream chunk arrives. No paid provider key was
used for these tests. Hosted provider live-account behavior remains separate
from SDK serialization certification.

The phase's SDK test file passes 46 cases, including 21 installed-SDK loopback
checks. Three of those exercise async Agent tool/final-stream runs. Eight media session tests
verify all four media types, text file content, repeated serialization, saved
file replay after local files are removed, and unchanged non-media byte handling.

Official sources checked on 2026-10-05:
[OpenAI Python async SDK](https://github.com/openai/openai-python),
[Responses media](https://developers.openai.com/api/docs/guides/images-vision),
[file inputs](https://developers.openai.com/api/docs/guides/file-inputs),
[structured output schemas](https://developers.openai.com/api/docs/guides/structured-outputs),
[Groq async SDK](https://github.com/groq/groq-python), and
[Groq tool use](https://console.groq.com/docs/tool-use/overview).
