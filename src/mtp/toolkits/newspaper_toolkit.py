from __future__ import annotations

from ..protocol import ToolRiskLevel, ToolSpec
from ..runtime import RegisteredTool, ToolkitLoader
from .common import allow_ref


class NewspaperToolkit(ToolkitLoader):
    def _extract_article_text(self, url: str) -> str:
        try:
            from newspaper import Article
        except ImportError as exc:
            raise ImportError(
                "NewspaperToolkit requires `newspaper3k` and `lxml_html_clean`. Install with: "
                "pip install newspaper3k lxml_html_clean"
            ) from exc

        article = Article(url)
        article.download()
        article.parse()
        return article.text

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="newspaper.get_article_text",
                description="Extract plain article text from a news URL via newspaper3k.",
                input_schema={
                    "type": "object",
                    "properties": {"url": allow_ref({"type": "string"})},
                    "required": ["url"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            )
        ]

    def load_tools(self) -> list[RegisteredTool]:
        def get_article_text(url: str) -> str:
            return self._extract_article_text(url=url)

        return [RegisteredTool(spec=self.list_tool_specs()[0], handler=get_article_text)]
