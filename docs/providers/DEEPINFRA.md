# DeepInfra

Install `pip install "mtpx[deepinfra]"` and set `DEEPINFRA_API_KEY`.

```python
from mtp.providers import DeepInfra

provider = DeepInfra(
    model="deepseek-ai/DeepSeek-V4-Flash-0731",
    max_tokens=512,
    parallel_tool_calls=True,
)
```

The default endpoint is `https://api.deepinfra.com/v1/openai`. Use the provider
with an MTP agent and registered tools. Native independent calls become parallel
batches; `$ref` arguments produce dependency-ordered batches when the tool
schema permits references. Use `run_loop_events` or `arun_loop_events` for native
streaming. Numeric output budgets apply to planning and finalization.

Use `mtp tui --backend deepinfra` or `/backend deepinfra`. Setup accepts the key,
model, endpoint, and output limit. The catalog uses the configured endpoint's
`/models` resource. Do not assume every listed model supports every tool option.

This adapter defaults to text input and client-side output validation. Optional
image input requires an explicitly compatible model and `input_modalities`.
Native SDK JSON/SSE serialization was tested through loopback fixtures; live
DeepInfra inference and multimodal behavior were not tested.

[Official tool-calling contract](https://docs.deepinfra.com/chat/tool-calling),
[shared options](../COMPATIBLE_PROVIDERS.md).
