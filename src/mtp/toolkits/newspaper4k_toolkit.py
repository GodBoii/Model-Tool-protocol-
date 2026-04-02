from __future__ import annotations

from typing import Any

from ..protocol import ToolRiskLevel, ToolSpec
from ..runtime import RegisteredTool, ToolkitLoader
from .common import allow_ref


class Newspaper4kToolkit(ToolkitLoader):
    def __init__(self, *, include_summary: bool = False, article_length: int | None = None) -> None:
        self.include_summary = include_summary
        self.article_length = article_length

    def _read_article_data(self, url: str, include_summary: bool) -> dict[str, Any]:
        try:
            import newspaper
        except ImportError as exc:
            raise ImportError(
                "Newspaper4kToolkit requires `newspaper4k` and `lxml_html_clean`. Install with: "
                "pip install newspaper4k lxml_html_clean"
            ) from exc

        article = newspaper.article(url)
        article_data: dict[str, Any] = {}

        if getattr(article, "title", None):
            article_data["title"] = article.title
        if getattr(article, "authors", None):
            article_data["authors"] = article.authors
        if getattr(article, "text", None):
            article_data["text"] = article.text
        if include_summary and getattr(article, "summary", None):
            article_data["summary"] = article.summary

        publish_date = getattr(article, "publish_date", None)
        if publish_date is not None:
            try:
                article_data["publish_date"] = publish_date.isoformat()
            except Exception:
                article_data["publish_date"] = str(publish_date)

        return article_data

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="newspaper4k.read_article",
                description="Read article metadata and text from URL via newspaper4k.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": allow_ref({"type": "string"}),
                        "include_summary": allow_ref({"type": "boolean"}),
                        "article_length": allow_ref({"type": "integer"}),
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            )
        ]

    def load_tools(self) -> list[RegisteredTool]:
        def read_article(
            url: str,
            include_summary: bool | None = None,
            article_length: int | None = None,
        ) -> dict[str, Any]:
            resolved_include_summary = self.include_summary if include_summary is None else include_summary
            resolved_article_length = self.article_length if article_length is None else article_length
            article_data = self._read_article_data(url=url, include_summary=resolved_include_summary)
            if resolved_article_length is not None and "text" in article_data:
                article_data["text"] = str(article_data["text"])[:resolved_article_length]
            return article_data

        return [RegisteredTool(spec=self.list_tool_specs()[0], handler=read_article)]
