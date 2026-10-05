# OpenAI Responses

This is an opt-in adapter. Existing `OpenAI` code continues to use Chat
Completions. Install `pip install "mtpx[openai-responses]"` and set
`OPENAI_API_KEY`.

```python
from mtp import Agent
from mtp.providers import OpenAIResponses
from mtp.toolkits import CalculatorToolkit

Agent.load_dotenv_if_available()
tools = Agent.ToolRegistry()
tools.register_toolkit_loader("calculator", CalculatorToolkit())
provider = OpenAIResponses(
    model="gpt-4o",
    max_output_tokens=1024,
    parallel_tool_calls=True,
)
agent = Agent(provider=provider, tools=tools)
print(agent.run_loop("Calculate 17+25.", max_rounds=3))
```

Set the model explicitly for your account. GPT-6 Astra and GPT-6.1 Sol tool
calling require Responses; the adapter sends Responses-native function schemas
and outputs. Sampling temperature is omitted by default. `reasoning_effort` is
optional and model-dependent. The `gpt-4o` default is a compatibility choice,
not a claim that it is the newest model.

Requests use `store=False` and request encrypted reasoning. MTP preserves every
returned reasoning, function-call, and assistant-message item as JSON-safe
`responses_items` in history. Subsequent requests replay those items alongside
`function_call_output` entries correlated by native `call_id`. Session storage
retains opaque encrypted content unchanged.

Native streaming uses typed SSE events. Text deltas appear as text events, but
tools execute only after `response.completed` provides complete output items.
Failed, incomplete, missing-terminal, or unfinished-call responses raise errors
instead of reporting successful execution. Function schemas set `strict=False`
explicitly to retain MTP's non-strict/reference-compatible contract.

Use `mtp tui --backend openai_responses` or `/backend openai_responses`. Setup
accepts model, endpoint, key, and maximum output tokens. SDK calls can provide
reasoning effort; no new model-specific effort dialog is added here.

This first Responses implementation advertises text input only, custom function
tools, and manually replayed history. Images/files, hosted tools, automatic
conversation IDs, native async SDK clients, and provider-enforced structured
outputs are outside this phase. Unknown native output-item types fail clearly.
Installed SDK JSON/SSE behavior passed local round-trip tests. No live OpenAI
key was used.

[Official migration and schema guidance](https://developers.openai.com/api/docs/guides/migrate-to-responses),
[reasoning replay](https://developers.openai.com/api/docs/guides/reasoning),
[SSE events](https://developers.openai.com/api/docs/guides/streaming-responses).
