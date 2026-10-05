# xAI Responses

`XAIResponsesToolCallingProvider` uses the OpenAI SDK's Responses client against
`https://api.x.ai/v1`. Provide an explicit model ID supported by your account.
MTP does not choose a rolling default or enable xAI's server-side tools.

```python
from mtp.providers.xai_provider import XAIResponsesToolCallingProvider

provider = XAIResponsesToolCallingProvider(
    model="your-xai-model-id",
    max_output_tokens=2048,
    native_async=True,
)
```

Set `XAI_API_KEY`, or pass `api_key`. MTP never substitutes `OPENAI_API_KEY`.
Injected sync and async clients are also supported. The provider label in
events, capabilities, and plans is `xai`.

MTP sends `store=False`, asks for `reasoning.encrypted_content`, and replays
native reasoning, assistant messages, function calls, and matching function
results from local session history. It executes client-side MTP tools only.
Web search, X search, code interpreter, collections, built-in MCP tools,
background processing, and stateful `previous_response_id` chaining are outside
this adapter. Supplying those through protocol-overriding `extra_body` fields
is rejected.

`native_async=True` selects the async OpenAI SDK. Otherwise MTP uses the existing
sync-to-async thread bridge. Both paths preserve streamed text and reasoning
events and authorize tools only from a completed terminal response. Model-specific
settings such as reasoning effort, temperature, parallel tools, image/file
input, and JSON-schema output must be supported by the selected xAI model. See
[Responses options](OPENAI_RESPONSES.md) for explicit multimodal and schema opt-ins.

Real SDK transport tests verify function IDs, JSON argument serialization,
encrypted reasoning replay, matching results, and provider labels. They use
synthetic keys and in-memory transport responses. Live xAI inference, model
availability, quotas, and billing were not verified. Release SDK clients created
by the adapter with `await provider.aclose()`.

Sources checked on 2026-10-05:
[xAI Responses and stateless replay](https://docs.x.ai/developers/model-capabilities/text/generate-text)
and [tool capabilities](https://docs.x.ai/developers/tools/overview).
