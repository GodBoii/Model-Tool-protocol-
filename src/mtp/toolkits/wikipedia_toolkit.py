from __future__ import annotations

from typing import Any

from ..protocol import ToolRiskLevel, ToolSpec
from ..runtime import RegisteredTool, ToolkitLoader
from .common import allow_ref


class WikipediaToolkit(ToolkitLoader):
    def __init__(self, *, default_sentences: int = 3, auto_suggest: bool = True) -> None:
        self.default_sentences = default_sentences
        self.auto_suggest = auto_suggest

    def _search_summary(self, query: str, sentences: int) -> str:
        try:
            import wikipedia
        except ImportError as exc:
            raise ImportError(
                "WikipediaToolkit requires the `wikipedia` package. Install it with: pip install wikipedia"
            ) from exc
        return wikipedia.summary(query, sentences=sentences, auto_suggest=self.auto_suggest)

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="wikipedia.search_wikipedia",
                description="Search Wikipedia and return a short summary for the query.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": allow_ref({"type": "string"}),
                        "sentences": allow_ref({"type": "integer"}),
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            )
        ]

    def load_tools(self) -> list[RegisteredTool]:
        def search_wikipedia(query: str, sentences: int | None = None) -> dict[str, Any]:
            resolved_sentences = self.default_sentences if sentences is None else sentences
            summary = self._search_summary(query=query, sentences=resolved_sentences)
            return {"query": query, "sentences": resolved_sentences, "summary": summary}

        return [RegisteredTool(spec=self.list_tool_specs()[0], handler=search_wikipedia)]
