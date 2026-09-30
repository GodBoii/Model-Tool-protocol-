from __future__ import annotations

import pytest

from mtp.streaming import merge_stream_text


@pytest.mark.parametrize(("chunks", "expected"), [
    (["", ""], ""),
    (["hello", ""], "hello"),
    (["", "hello"], "hello"),
    (["ha", "ha"], "haha"),
    (["in the", " the box"], "in the the box"),
    (["hello", "a hello"], "helloa hello"),
    (["The cat", " sat on", " the", " mat", ",", " the", " end", "."], "The cat sat on the mat, the end."),
    (["Hello", "\n", "\n", "- one", "\n", "- one"], "Hello\n\n- one\n- one"),
    (["step", " step", " step"], "step step step"),
    (["猫", "猫", "\n", "猫"], "猫猫\n猫"),
    (["a" * 31, "a" * 31], "a" * 62),
    (["a" * 32, "a" * 32], "a" * 32),
    (["a" * 32, "a" * 32 + " more"], "a" * 32 + " more"),
    (["a" * 32, "a"], "a" * 33),
])
def test_merge_stream_text(chunks: list[str], expected: str) -> None:
    text = ""
    for chunk in chunks:
        text = merge_stream_text(text, chunk)
    assert text == expected
