# Vertex AI Gemini

`VertexGeminiToolCallingProvider` uses Google's `google-genai` SDK with
`vertexai=True`. It reuses MTP's Gemini message conversion and native thought
signature replay. An explicit Vertex model, Google Cloud project, and location
are required.

```python
from mtp.providers.vertex_provider import VertexGeminiToolCallingProvider

provider = VertexGeminiToolCallingProvider(
    model="your-vertex-gemini-model-id",
    project="your-project-id",
    location="global",
    native_async=True,
)
```

Install `google-genai`. Set `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` if
you omit `project` or `location`. Authentication uses Application Default
Credentials, or pass a Google credentials object through `credentials`.
This provider does not use a Gemini Developer API key, configure IAM, enable the
Vertex API, log in, or create a project.

The chosen project must have the Vertex API enabled and its identity must have
permission to invoke the selected model. Regional and `global` endpoints have
different model availability and data-residency behavior. Choose the location
explicitly according to your requirements.

The provider preserves Gemini native function-call IDs, assistant parts, and
thought signatures in local session history. It supports MTP parallel and
dependency batches through the existing Gemini converter. Truncated, blocked,
malformed, or missing candidates never produce executable tool plans.

`native_async=True` uses `client.aio.models.generate_content`, preserving the
same response decoding and session history. With the default `False`, MTP uses
the existing Gemini thread bridge. Injected clients are caller-owned. Release
SDK clients created by the adapter with `await provider.aclose()`.
`max_output_tokens` defaults to 1024 and can be set explicitly to a positive
integer or `None`. `temperature` accepts finite values from zero to two.

Supported media follow MTP's Gemini converter and the selected Vertex model.
This initial adapter does not stream the model's final response natively; its
capabilities advertise the explicit finalization fallback. It does not expose
Vertex-specific grounding, tuning, batch jobs, cached contexts, or managed agent
services.

Tests exercise native SDK-shaped messages, thought signatures after JSON session
replay, provider labels, complete-candidate enforcement, and direct native async
dispatch without mutating the shared client. No Vertex inference request or ADC
login was made. Account permissions, live model availability, regional service
behavior, and billing remain unverified.

Sources checked on 2026-10-05:
[Vertex Gemini quickstart](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/quickstart)
and [Google Gen AI SDK](https://googleapis.github.io/python-genai/).
