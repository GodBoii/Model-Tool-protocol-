# Azure OpenAI and Microsoft Foundry

`AzureOpenAIResponsesToolCallingProvider` uses Azure's current `/openai/v1`
Responses endpoint with the standard OpenAI SDK. It supports Azure OpenAI and
Foundry resource hosts. The `model` argument is your deployed model's deployment
name, not a universal model default. Availability and tools depend on that
deployment and resource region.

```python
from mtp.providers.azure_provider import AzureOpenAIResponsesToolCallingProvider

provider = AzureOpenAIResponsesToolCallingProvider(
    model="my-deployment-name",
    endpoint="https://my-resource.openai.azure.com",
    max_output_tokens=2048,
    native_async=True,
)
```

Set `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT`, or pass `api_key` and
`endpoint`. The endpoint may include `/openai/v1`; MTP adds it to a resource root.
It rejects deployment URLs, credentials embedded in URLs, query strings, and
insecure remote HTTP. This v1 adapter does not add a dated `api_version` query or
use the legacy deployment-scoped Chat Completions endpoint.

For Microsoft Entra ID, install `azure-identity`, configure an identity with
access to the resource, and pass `use_entra=True`. MTP constructs
`DefaultAzureCredential` with the `https://ai.azure.com/.default` scope through
`get_bearer_token_provider`. You can instead provide a synchronous refreshable
`token_provider` callback. API-key authentication and Entra authentication are
mutually exclusive. The SDK invokes the callback for outgoing requests; MTP
does not capture one token at initialization. The native async path awaits a
thread-backed credential callback instead of blocking the async SDK's request.

Requests use `store=False` and replay native Responses output items, encrypted
reasoning, and matching function results through local MTP session history.
The completed terminal response authorizes tool execution; truncated or failed
streams never produce an executable plan. Common Responses options include
`max_output_tokens`, `reasoning_effort`, `parallel_tool_calls`,
`timeout_seconds`, and injected `client` or `async_client`. Optional image/file
input and output schemas use the explicit opt-ins documented in
[the Responses guide](OPENAI_RESPONSES.md), and require deployment support.

Call `await provider.aclose()` to release SDK clients and credentials created
by the adapter. Caller-injected clients and token providers remain caller-owned.
No Azure billing request was made during verification. Real SDK transport tests
check v1 request paths, deployed model serialization, provider metadata, and
token refresh for synchronous and native asynchronous requests.

Sources checked on 2026-10-05:
[Azure Responses](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses),
[v1 migration](https://learn.microsoft.com/en-us/azure/developer/ai/how-to/azure-openai-to-responses),
and [Foundry Entra authentication](https://learn.microsoft.com/en-us/azure/ai-foundry/foundry-models/how-to/configure-entra-id).
