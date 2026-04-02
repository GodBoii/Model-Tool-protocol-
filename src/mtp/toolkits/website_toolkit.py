from __future__ import annotations

from typing import Any

from ..protocol import ToolRiskLevel, ToolSpec
from ..runtime import RegisteredTool, ToolkitLoader
from .common import allow_ref


class WebsiteToolkit(ToolkitLoader):
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        user_agent: str = "MTP-WebsiteToolkit/1.0",
        default_max_length: int = 5000,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.default_max_length = default_max_length

    def _read_website(self, url: str, max_length: int) -> dict[str, Any]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise ImportError(
                "WebsiteToolkit requires `requests` and `beautifulsoup4`. Install with: "
                "pip install requests beautifulsoup4"
            ) from exc

        response = requests.get(
            url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else None
        text = " ".join(soup.stripped_strings)
        if max_length > 0:
            text = text[:max_length]
        return {"url": url, "title": title, "text": text}

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="website.read_url",
                description="Read a URL and return extracted page text.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": allow_ref({"type": "string"}),
                        "max_length": allow_ref({"type": "integer"}),
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            )
        ]

    def load_tools(self) -> list[RegisteredTool]:
        def read_url(url: str, max_length: int | None = None) -> dict[str, Any]:
            resolved_max_length = self.default_max_length if max_length is None else max_length
            return self._read_website(url=url, max_length=resolved_max_length)

        return [RegisteredTool(spec=self.list_tool_specs()[0], handler=read_url)]
