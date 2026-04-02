from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.protocol import ToolCall
from mtp.runtime import ToolRegistry
from mtp.toolkits import (
    Crawl4aiToolkit,
    Newspaper4kToolkit,
    NewspaperToolkit,
    WebsiteToolkit,
    WikipediaToolkit,
)


class WebToolkitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_specs_are_discoverable_with_lazy_loaders(self) -> None:
        registry = ToolRegistry()
        registry.register_toolkit_loader("wikipedia", WikipediaToolkit())
        registry.register_toolkit_loader("website", WebsiteToolkit())
        registry.register_toolkit_loader("newspaper", NewspaperToolkit())
        registry.register_toolkit_loader("newspaper4k", Newspaper4kToolkit())
        registry.register_toolkit_loader("crawl4ai", Crawl4aiToolkit())

        tool_names = {tool.name for tool in registry.list_tools()}
        self.assertIn("wikipedia.search_wikipedia", tool_names)
        self.assertIn("website.read_url", tool_names)
        self.assertIn("newspaper.get_article_text", tool_names)
        self.assertIn("newspaper4k.read_article", tool_names)
        self.assertIn("crawl4ai.web_crawler", tool_names)

    async def test_wikipedia_tool_executes_with_stubbed_backend(self) -> None:
        toolkit = WikipediaToolkit()
        toolkit._search_summary = lambda query, sentences: f"{query}:{sentences}"  # type: ignore[method-assign]
        registry = ToolRegistry()
        registry.register_toolkit_loader("wikipedia", toolkit)

        result = await registry.execute_call(
            ToolCall(id="w1", name="wikipedia.search_wikipedia", arguments={"query": "Python"}),
            {},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output["summary"], "Python:3")

    async def test_website_tool_executes_with_stubbed_backend(self) -> None:
        toolkit = WebsiteToolkit()
        toolkit._read_website = lambda url, max_length: {"url": url, "title": "t", "text": f"len={max_length}"}  # type: ignore[method-assign]
        registry = ToolRegistry()
        registry.register_toolkit_loader("website", toolkit)

        result = await registry.execute_call(
            ToolCall(id="s1", name="website.read_url", arguments={"url": "https://example.com", "max_length": 120}),
            {},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output["text"], "len=120")

    async def test_newspaper_tool_executes_with_stubbed_backend(self) -> None:
        toolkit = NewspaperToolkit()
        toolkit._extract_article_text = lambda url: f"article:{url}"  # type: ignore[method-assign]
        registry = ToolRegistry()
        registry.register_toolkit_loader("newspaper", toolkit)

        result = await registry.execute_call(
            ToolCall(id="n1", name="newspaper.get_article_text", arguments={"url": "https://news.example/article"}),
            {},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output, "article:https://news.example/article")

    async def test_newspaper4k_tool_executes_with_stubbed_backend(self) -> None:
        toolkit = Newspaper4kToolkit()
        toolkit._read_article_data = lambda url, include_summary: {  # type: ignore[method-assign]
            "title": "Story",
            "text": "abcdef",
            "summary": "sum" if include_summary else None,
            "url": url,
        }
        registry = ToolRegistry()
        registry.register_toolkit_loader("newspaper4k", toolkit)

        result = await registry.execute_call(
            ToolCall(
                id="n4",
                name="newspaper4k.read_article",
                arguments={"url": "https://news.example/article", "article_length": 3, "include_summary": True},
            ),
            {},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output["text"], "abc")
        self.assertEqual(result.output["summary"], "sum")

    async def test_crawl4ai_tool_executes_with_stubbed_backend(self) -> None:
        toolkit = Crawl4aiToolkit()

        async def _stub(url: str, search_query: str | None, max_length: int) -> str:
            return f"url={url}|query={search_query}|max={max_length}"

        toolkit._crawl_url = _stub  # type: ignore[method-assign]
        registry = ToolRegistry()
        registry.register_toolkit_loader("crawl4ai", toolkit)

        result = await registry.execute_call(
            ToolCall(
                id="c1",
                name="crawl4ai.web_crawler",
                arguments={"url": "https://example.com/docs", "search_query": "agent"},
            ),
            {},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output, "url=https://example.com/docs|query=agent|max=1000")


if __name__ == "__main__":
    unittest.main()
