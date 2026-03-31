from __future__ import annotations

import json
from typing import Any

from ..agent import AgentAction, ProviderAdapter
from ..config import require_env
from ..protocol import ExecutionPlan, ToolBatch, ToolCall, ToolResult, ToolSpec


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
        except ImportError:
            raise ImportError(
                "google-genai is not installed. Gemini provider requires: pip install google-genai"
            )

        key = api_key or require_env("GEMINI_API_KEY")
        return genai.Client(api_key=key)

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentAction:
        user_text = messages[-1]["content"] if messages else ""
        
        # Format tools for Google's API
        genai_tools = []
        if tools:
            functions = []
            for tool in tools:
                functions.append({
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                })
            genai_tools = [{"function_declarations": functions}]

        response = self._client.models.generate_content(
            model=self.model_name,
            contents=user_text,
            config={
                "tools": genai_tools if genai_tools else None,
                "temperature": self.temperature,
            }
        )
        
        # Check for function calls
        calls = []
        if response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if fn := part.function_call:
                    calls.append(
                        ToolCall(
                            id=f"gemini_call_{len(calls)}",
                            name=fn.name,
                            arguments=dict(fn.args),
                        )
                    )

        if calls:
            plan = ExecutionPlan(
                batches=[ToolBatch(mode="sequential", calls=calls)],
                metadata={"provider": "gemini", "model": self.model_name}
            )
            return AgentAction(plan=plan)

        return AgentAction(response_text=response.text)

    def finalize(self, messages: list[dict[str, Any]], tool_results: list[ToolResult]) -> str:
        user_text = messages[0]["content"] if messages else ""
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=user_text
        )
        return response.text
