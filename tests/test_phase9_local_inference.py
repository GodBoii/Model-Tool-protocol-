"""Explicit opt-in local model execution, with no inference-account credentials."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


def test_real_ollama_chat_and_stream():
    model = os.getenv("MTP_TEST_OLLAMA_MODEL")
    if not model:
        pytest.skip("Set MTP_TEST_OLLAMA_MODEL after installing a local model.")
    ollama = pytest.importorskip("ollama")
    client = ollama.Client(
        host=os.getenv("MTP_TEST_OLLAMA_HOST", "http://127.0.0.1:11434"), timeout=120
    )
    response = client.chat(
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word ready."}],
        think=False,
        options={"num_predict": 64, "temperature": 0},
        stream=False,
    )
    assert response.done and response.message.content.strip()
    from mtp.providers import Ollama

    provider = Ollama(model=model, client=client, think=False, options={"num_predict":64, "temperature":0})
    # Exercise the production adapter, including its actual stream decoder.
    events = list(
        provider.next_action_stream(
            [{"role": "user", "content": "/no_think\nReply with the word ready."}], []
        )
    )
    from mtp.agent import AgentAction

    assert isinstance(events[-1], AgentAction) and events[-1].response_text.strip()
