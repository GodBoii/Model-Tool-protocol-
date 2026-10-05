"""Google Vertex Gemini through google-genai and Application Default Credentials."""

from __future__ import annotations

import asyncio
import copy
import math
import os
import re
from types import SimpleNamespace
from typing import Any

from ..agent import AgentAction
from ..protocol import ToolResult, ToolSpec
from .gemini_provider import GeminiToolCallingProvider


class VertexGeminiToolCallingProvider(GeminiToolCallingProvider):
    def __init__(
        self,
        *,
        model: str,
        project: str | None = None,
        location: str | None = None,
        credentials: Any | None = None,
        temperature: float = 0.0,
        max_output_tokens: int | None = 1024,
        client: Any | None = None,
        native_async: bool = False,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError(
                "model must be an explicit nonempty Vertex Gemini model ID."
            )
        if type(native_async) is not bool:
            raise TypeError("native_async must be a boolean.")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (float, int))
            or not math.isfinite(temperature)
            or not 0 <= temperature <= 2
        ):
            raise ValueError("temperature must be finite and between zero and two.")
        if max_output_tokens is not None and (
            type(max_output_tokens) is not int or max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be a positive integer or None.")
        self.max_output_tokens = max_output_tokens
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION")
        if not self.project or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9:._-]{0,254}", self.project
        ):
            raise ValueError(
                "Set project or GOOGLE_CLOUD_PROJECT to your Google Cloud project ID."
            )
        if not self.location or not re.fullmatch(
            r"[a-z][a-z0-9-]{0,62}", self.location
        ):
            raise ValueError(
                "Set location or GOOGLE_CLOUD_LOCATION to a Vertex region or global."
            )
        self.native_async = native_async
        self._owns_client = client is None
        if client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise ImportError("Install google-genai for Vertex Gemini.") from exc
            client = genai.Client(
                vertexai=True,
                project=self.project,
                location=self.location,
                credentials=credentials,
            )
        if native_async and not hasattr(client, "aio"):
            raise ValueError(
                "Native Vertex async requires the google-genai aio client."
            )
        super().__init__(model=model, temperature=temperature, client=client)

    def _label(self, action: AgentAction) -> AgentAction:
        action.metadata["provider"] = "vertex"
        if action.plan is not None:
            action.plan.metadata["provider"] = "vertex"
        return action

    def next_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        response = self._client.models.generate_content(
            **self._request(messages, tools)
        )
        return self._decode(response, messages, tools)

    def _request(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> dict[str, Any]:
        contents, system = self._to_gemini_payload(messages)
        config: dict[str, Any] = {"temperature": self.temperature}
        if self.max_output_tokens is not None:
            config["max_output_tokens"] = self.max_output_tokens
        if system:
            config["system_instruction"] = system
        if tools:
            config["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": self._sanitize_schema_for_gemini(
                                tool.input_schema
                                or {"type": "object", "properties": {}}
                            ),
                        }
                        for tool in tools
                    ]
                }
            ]
        return {"model": self.model_name, "contents": contents, "config": config}

    def _decode(
        self, response: Any, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise ValueError("Vertex returned no complete candidate.")
        reason = getattr(candidates[0], "finish_reason", None)
        reason = getattr(reason, "value", reason)
        if reason not in {None, "STOP"}:
            raise ValueError(f"Vertex candidate did not complete safely: {reason!r}")
        # Reuse Gemini's signature-preserving decoder without mutating the live
        # client while another request may be running.
        decoder = copy.copy(self)
        decoder._client = SimpleNamespace(
            models=SimpleNamespace(generate_content=lambda **kwargs: response)
        )
        return self._label(
            GeminiToolCallingProvider.next_action(decoder, messages, tools)
        )

    def finalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        action = self.next_action(messages, [])
        if action.plan:
            raise ValueError("Vertex returned tool calls during finalization.")
        self._last_finalize_message = action.metadata["assistant_message"]
        self._last_finalize_usage = action.metadata.get("usage")
        return action.response_text or "Done."

    async def anext_action(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AgentAction:
        if not self.native_async:
            return await super().anext_action(messages, tools)
        response = await self._client.aio.models.generate_content(
            **self._request(messages, tools)
        )
        return self._decode(response, messages, tools)

    async def afinalize(
        self, messages: list[dict[str, Any]], tool_results: list[ToolResult]
    ) -> str:
        if not self.native_async:
            return await super().afinalize(messages, tool_results)
        action = await self.anext_action(messages, [])
        if action.plan:
            raise ValueError("Vertex returned tool calls during finalization.")
        self._last_finalize_message = action.metadata["assistant_message"]
        self._last_finalize_usage = action.metadata.get("usage")
        return action.response_text or "Done."

    def capabilities(self):
        result = super().capabilities()
        result.provider = "vertex"
        result.supports_native_async = self.native_async
        return result

    async def aclose(self) -> None:
        if not self._owns_client:
            return
        if self.native_async:
            await self._client.aio.aclose()
        await asyncio.to_thread(self._client.close)
        self._owns_client = False
