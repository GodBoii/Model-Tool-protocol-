"""Incremental Markdown rendering for streamed assistant text.

Re-parsing the whole reply on every frame costs O(reply length) per frame.
``StreamingMarkdown`` splits the text at paragraph breaks outside code
fences. Everything before the last break is rendered once into frozen
segments; only the trailing paragraph is re-parsed while tokens arrive.
"""
from __future__ import annotations

import re

from rich.markdown import Markdown as RichMarkdown
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def stable_prefix_end(text: str) -> int:
    """Return the offset where the stable Markdown prefix of ``text`` ends.

    The prefix ends just after the last blank line that is outside a fenced
    code block. Text before that offset forms complete blocks that later
    tokens cannot change. Returns 0 when there is no such break yet.
    """
    end = 0
    offset = 0
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        next_offset = offset + len(line)
        # A line without its newline may still be growing; ignore it.
        if not line.endswith("\n"):
            break
        match = _FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not line.strip()[len(marker):]:
                fence = None
        elif fence is None and not line.strip():
            end = next_offset
        offset = next_offset
    return end


def _markdown(text: str) -> RichMarkdown:
    return RichMarkdown(text, code_theme="monokai")


class StreamingMarkdown(Vertical):
    """Markdown view that only re-renders the unfinished tail on update."""

    DEFAULT_CSS = """
    StreamingMarkdown {
        height: auto;
    }
    StreamingMarkdown > .md-segment {
        height: auto;
        margin: 0 0 1 0;
    }
    StreamingMarkdown > .md-tail {
        height: auto;
    }
    """

    def __init__(self, text: str = "", **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._pending = text
        self._text = ""
        self._stable_end = 0
        self._segments: list[Static] = []
        self._tail: Static | None = None
        self._tail_source: str | None = None
        self._ready = False

    @property
    def text(self) -> str:
        return self._text

    def compose(self) -> ComposeResult:
        self._tail = Static(classes="md-tail")
        yield self._tail

    def on_mount(self) -> None:
        # Textual flips ``is_mounted`` only after on_mount returns, so track readiness here.
        self._ready = True
        self.update_text(self._pending)

    def update_text(self, text: str) -> None:
        if not self._ready or self._tail is None:
            self._pending = text
            return
        if text == self._text:
            return
        if not text.startswith(self._text[: self._stable_end]):
            # Text was replaced, not extended: start over.
            for segment in self._segments:
                segment.remove()
            self._segments = []
            self._stable_end = 0
        self._text = text

        new_end = stable_prefix_end(text)
        if new_end > self._stable_end:
            chunk = text[self._stable_end:new_end].strip("\n")
            if chunk:
                segment = Static(_markdown(chunk), classes="md-segment")
                self._segments.append(segment)
                self.mount(segment, before=self._tail)
            self._stable_end = new_end

        tail = text[self._stable_end:]
        if tail != self._tail_source:
            self._tail_source = tail
            self._tail.update(_markdown(tail) if tail.strip() else "")
            self._tail.display = bool(tail.strip())
