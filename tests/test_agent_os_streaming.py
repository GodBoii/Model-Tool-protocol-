from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from mtp.agent_os.app import RunViewState, _consume_event, _merge_stream_text
from mtp.cli.tui_mtp_backend import _merge_stream_text as tui_merge
from mtp.streaming import merge_stream_text


def test_clients_share_the_stream_merger() -> None:
    assert _merge_stream_text is tui_merge is merge_stream_text


@pytest.mark.parametrize("source", ["direct", "finalize_stream", "finalize_fallback"])
def test_agent_os_keeps_repeated_text_tokens(source: str) -> None:
    state = RunViewState(saw_tool_round=source != "direct")
    chunks = ["Hello", "\n", "\n", "- one", "\n", "- one", " the", " the"]
    for chunk in chunks:
        _consume_event(state, {"type": "text_chunk", "chunk": chunk, "source": source})
    assert "".join(state.final_text_chunks) == "".join(chunks)


def test_agent_os_keeps_repeated_thinking_tokens() -> None:
    state = RunViewState()
    for chunk in ["step", " step", " step"]:
        _consume_event(state, {"type": "reasoning_chunk", "chunk": chunk})
    assert state.thinking_text == "step step step"
