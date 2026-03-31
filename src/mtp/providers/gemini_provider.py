from __future__ import annotations

from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .common import calls_to_dependency_batches, extract_refs, normalize_refs


class GeminiToolCallingProvider(ProviderAdapter):
    """
    Provider adapter for Google Gemini.
    Uses the modern google.genai SDK.
    """

    def __init__(
        self,
        *,
        model: str = "gemini-2.0-flash",
        api_key: str | None = None,
        temperature: float = 0.0,
        client: Any | None = None,
    ) -> None:
        self.model_name = model
        self.temperature = temperature
        self._client = client or self._make_client(api_key=api_key)

    def _make_client(self, api_key: str | None) -> Any:
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "`google-genai` not installed. Please install using `pip install google-genai`"
            ) from exc

        key = api_key or require_env("GEMINI_API_KEY")
        return genai.Client(api_key=key)

    def _to_gemini_prompt(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if role == "tool":
                lines.append(f"tool_result ({msg.get('tool_name','tool')}): {content}")
            else:
                lines.append(f"{role}: {content}")
        return "\n".join(lines).strip()

    def _is_ref_schema(self, schema: dict[str, Any]) -> bool:
        props = schema.get("properties")
        required = schema.get("required")
        return (
            schema.get("type") == "object"
            and isinstance(props, dict)
            and "$ref" in props
            and isinstance(required, list)
            and "$ref" in required
        )

    def _sanitize_schema_for_gemini(self, schema: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = {"type", "properties", "required", "items", "description", "enum", "nullable"}
        sanitized: dict[str, Any] = {}

        for key, value in schema.items():
            if key not in allowed_keys:
                continue
            if key == "properties" and isinstance(value, dict):
                props: dict[str, Any] = {}
                for prop_name, prop_schema in value.items():
                    if isinstance(prop_schema, dict):
                        props[prop_name] = self._sanitize_schema_for_gemini(prop_schema)
                sanitized["properties"] = props
            elif key == "items" and isinstance(value, dict):
                sanitized["items"] = self._sanitize_schema_for_gemini(value)
            else:
                sanitized[key] = value

        any_of = schema.get("anyOf")
        if isinstance(any_of, list) and any_of:
            non_ref_options = [
                option
                for option in any_of
                if isinstance(option, dict) and not self._is_ref_schema(option)
            ]
            chosen = non_ref_options[0] if non_ref_options else next(
                (option for option in any_of if isinstance(option, dict)),
                None,
            )
            if isinstance(chosen, dict):
                return self._sanitize_schema_for_gemini(chosen)

        if "type" not in sanitized:
            sanitized["type"] = "object"

        return sanitized

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        prompt = self._to_gemini_prompt(messages)

        genai_tools: list[dict[str, Any]] = []
        if tools:
            functions = []
            for tool in tools:
                functions.append({
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": self._sanitize_schema_for_gemini(
                        tool.input_schema or {"type": "object", "properties": {}}
                    ),
                })
            genai_tools = [{"function_declarations": functions}]

        config: dict[str, Any] = {"temperature": self.temperature}
        if genai_tools:
            config["tools"] = genai_tools

        response = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=config,
        )

        calls: list[ToolCall] = []
        serialized_tool_calls: list[dict[str, Any]] = []
        id_by_index: dict[int, str] = {}
        if response.candidates[0].content.parts:
            for idx, part in enumerate(response.candidates[0].content.parts):
                if fn := part.function_call:
                    call_id = f"gemini_call_{idx}"
                    id_by_index[idx] = call_id
                    raw_args = dict(fn.args)
                    normalized_args = normalize_refs(raw_args, id_by_index)
                    depends_on = list(dict.fromkeys(extract_refs(normalized_args)))
                    calls.append(
                        ToolCall(
                            id=call_id,
                            name=fn.name,
                            arguments=normalized_args,
                            depends_on=depends_on,
                        )
                    )
                    serialized_tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": fn.name, "arguments": str(raw_args)},
                        }
                    )

        if calls:
            plan = ExecutionPlan(
                batches=calls_to_dependency_batches(calls),
                metadata={"provider": "gemini", "model": self.model_name}
            )
            return AgentAction(
                plan=plan,
                metadata={
                    "assistant_tool_message": {
                        "role": "assistant",
                        "content": getattr(response, "text", "") or "",
                        "tool_calls": serialized_tool_calls,
                    }
                },
            )

        return AgentAction(response_text=getattr(response, "text", "") or "")

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        prompt = self._to_gemini_prompt(messages)
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"temperature": self.temperature},
        )
        return getattr(response, "text", "") or "Done."
