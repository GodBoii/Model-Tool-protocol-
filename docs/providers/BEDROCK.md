# Amazon Bedrock Converse

`BedrockConverseToolCallingProvider` uses boto3's `bedrock-runtime` client with
`Converse` and `ConverseStream`. Model IDs, inference-profile IDs, or ARNs must be
explicit. The adapter does not substitute an Anthropic HTTP endpoint or infer
which models your AWS account can invoke.

```python
from mtp.providers.bedrock_provider import BedrockConverseToolCallingProvider

provider = BedrockConverseToolCallingProvider(
    model="your-bedrock-model-or-inference-profile-id",
    region="us-east-1",
    max_tokens=2048,
)
```

Install `boto3`. Authentication uses its standard credential chain, including
AWS profiles, workload identity, or configured temporary credentials. `profile`
is optional. Set `region`, `AWS_REGION`, or `AWS_DEFAULT_REGION`. MTP does not
manage IAM grants, request model access, create AWS resources, or persist AWS
credentials. Your account needs permission to invoke the configured model;
streaming additionally requires the model's streaming support and appropriate
Bedrock permissions.

The initial implementation supports text input and client-side tools. Tool names
use a reversible wire-name mapping to satisfy Bedrock identifier restrictions.
Calls keep `toolUseId`, and results become user-role `toolResult` blocks matching
those IDs. Native assistant blocks and signed reasoning replay without text
reconstruction. Redacted binary reasoning survives JSON-backed sessions through
an explicit base64 record and decodes back to bytes for the SDK.

Several independent tool calls create a parallel MTP batch. `$ref` dependencies
create ordered batches. Tool inputs must be complete JSON objects with unique,
nonempty IDs. `tool_use` must match the presence of tool calls. `max_tokens`,
guardrail intervention, filtering, malformed output, missing terminal events,
unfinished content blocks, and model-window exhaustion fail before tool execution.
Streaming closes the SDK event stream on success or failure.

Options include `max_tokens`, `temperature` from zero to one, `timeout_seconds`,
`tool_choice="auto"` or `"any"`, a forced `{"tool": {"name": "registered.name"}}`,
and model-specific `additional_model_request_fields`. Forced tool use and custom
fields are model-dependent. The adapter sends two standard retry attempts and
applies connection/read deadlines when it creates the boto3 client. Injected
clients retain their caller-defined SDK policy.

Converse has native synchronous event streaming. boto3 itself is synchronous, so
MTP's asynchronous methods use its bounded thread bridge and advertise
`supports_native_async=False`. `await provider.aclose()` closes only clients the
adapter created. Media, server-side tool execution, guardrail configuration,
prompt-management templates, and managed async inference are outside this
initial adapter.

Tests validate real boto3 request/response schemas using botocore Stubber and
event schemas. They also exercise native history, signed reasoning, redacted
bytes, dependency batches, stream fragments, truncated responses, and cleanup.
No AWS inference request or credential lookup was used by those tests. Account
model access, IAM policy, regional availability, and live inference remain
unverified.

Sources checked on 2026-10-05:
[Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html),
[ConverseStream API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html),
and [Bedrock tool use](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html).
