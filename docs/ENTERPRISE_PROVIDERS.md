# Enterprise provider adapters

MTP adds four explicit deployment/account adapters. None chooses a universal
model default or configures cloud resources.

| Provider | Adapter | Required configuration | Authentication | Async behavior |
| --- | --- | --- | --- | --- |
| [Azure OpenAI / Foundry](providers/AZURE.md) | `AzureOpenAIResponsesToolCallingProvider` | Deployment name and resource endpoint | Azure API key or refreshed Entra callback | Native OpenAI async SDK opt-in |
| [xAI](providers/XAI.md) | `XAIResponsesToolCallingProvider` | Account-supported model ID | `XAI_API_KEY` | Native OpenAI async SDK opt-in |
| [Amazon Bedrock](providers/BEDROCK.md) | `BedrockConverseToolCallingProvider` | Model/profile ID or ARN and AWS region | boto3 credential chain | Sync SDK with async thread bridge |
| [Vertex AI](providers/VERTEX.md) | `VertexGeminiToolCallingProvider` | Vertex model, project, and location | Application Default Credentials | Native google-genai async SDK opt-in |

Azure and xAI share MTP's stateless Responses protocol. Bedrock uses its native
Converse tool blocks and event stream. Vertex reuses Gemini's native message and
thought-signature handling. All maintain the native IDs and replay records needed
for subsequent tool rounds and local session persistence.

Tests use synthetic credentials with real SDK transports, botocore schema
validation, and local fixtures. They do not certify cloud accounts, deployment
permissions, paid inference, or model availability. Live Groq verification of
the shared adapter is recorded separately and does not prove these vendors'
account-specific behavior.
