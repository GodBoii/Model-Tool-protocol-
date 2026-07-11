from __future__ import annotations

import pytest
from rich.markdown import Markdown
from rich.text import Text
from textual.app import App, ComposeResult

from mtp.cli.tui_widgets.chat_log import AssistantMessageWidget, ChatMessage


class _WidgetApp(App[None]):
    def __init__(self, message: ChatMessage) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield AssistantMessageWidget(self.message)


@pytest.mark.asyncio
async def test_live_text_updates_existing_widget_without_markdown_reparse() -> None:
    first = ChatMessage(
        "assistant",
        "",
        is_live=True,
        assistant_blocks=[{"type": "text", "text": "one"}],
    )
    app = _WidgetApp(first)

    async with app.run_test() as pilot:
        assistant = app.query_one(AssistantMessageWidget)
        original = assistant._block_widgets[0]
        assert isinstance(original.content, Text)

        assistant.update_message(
            ChatMessage(
                "assistant",
                "",
                is_live=True,
                assistant_blocks=[{"type": "text", "text": "one two"}],
            )
        )
        await pilot.pause()

        assert assistant._block_widgets[0] is original
        assert isinstance(original.content, Text)
        assert original.content.plain == "one two"


@pytest.mark.asyncio
async def test_completed_text_gets_one_full_markdown_render_in_place() -> None:
    live = ChatMessage(
        "assistant",
        "",
        is_live=True,
        assistant_blocks=[{"type": "text", "text": "**done**"}],
    )
    app = _WidgetApp(live)

    async with app.run_test() as pilot:
        assistant = app.query_one(AssistantMessageWidget)
        original = assistant._block_widgets[0]
        assistant.update_message(
            ChatMessage(
                "assistant",
                "",
                is_live=False,
                assistant_blocks=[{"type": "text", "text": "**done**"}],
            )
        )
        await pilot.pause()

        assert assistant._block_widgets[0] is original
        assert isinstance(original.content, Markdown)


@pytest.mark.asyncio
async def test_appending_block_preserves_existing_widgets() -> None:
    live = ChatMessage(
        "assistant",
        "",
        is_live=True,
        assistant_blocks=[{"type": "thinking", "text": "hmm"}],
    )
    app = _WidgetApp(live)

    async with app.run_test() as pilot:
        assistant = app.query_one(AssistantMessageWidget)
        first_widget = assistant._block_widgets[0]
        assistant.update_message(
            ChatMessage(
                "assistant",
                "",
                is_live=True,
                assistant_blocks=[
                    {"type": "thinking", "text": "hmm"},
                    {"type": "text", "text": "answer"},
                ],
            )
        )
        await pilot.pause()

        assert len(assistant._block_widgets) == 2
        assert assistant._block_widgets[0] is first_widget
