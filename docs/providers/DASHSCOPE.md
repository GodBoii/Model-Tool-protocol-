# DashScope / Alibaba Cloud Model Studio

Install `pip install "mtpx[dashscope]"` and set `DASHSCOPE_API_KEY` from the
region you will call. Regional keys are not interchangeable.

```python
from mtp.providers import DashScope

provider = DashScope(
    model="qwen-plus",
    region="singapore",
    workspace_id="your-workspace-id",
    max_tokens=512,
    parallel_tool_calls=True,
)
```

Workspace endpoints support `singapore`, `beijing`, `hongkong`, and `tokyo`.
`virginia` uses `https://dashscope-us.aliyuncs.com/compatible-mode/v1`. You may
provide `base_url` directly instead of `workspace_id`. The historical Beijing
and Singapore endpoints remain defaults for compatibility when no workspace ID
is supplied; prefer your workspace-specific URL for new deployments.

Thinking defaults to disabled and is sent in `extra_body.enable_thinking`.
`enable_thinking=True` requires a streaming agent API. Synchronous planning or
finalization raises a clear error rather than sending an unsupported request.
Not every model accepts this flag; use a model with configurable thinking.

Use `mtp tui --backend dashscope` or `/backend dashscope`. Paste the full regional
URL in provider setup, enter the matching key/model, and set the output budget.
Catalog GET requests use that saved URL rather than the default Singapore URL.
Thinking can be configured through SDK options; this phase adds no dedicated
DashScope thinking dialog.

The adapter is text-only by default. Phase two verifies serialization, native
calls, streaming, references, regional configuration, and credential placement
against local fixtures. Live Alibaba inference is not verified.

[Official endpoint/region guidance](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope),
[function calling and thinking](https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling).
