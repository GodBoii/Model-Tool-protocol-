from __future__ import annotations

from types import SimpleNamespace

from mtp.providers.mistral_provider import MistralToolCallingProvider


class _Chat:
    def __init__(self) -> None:
        self.request = None

    def stream(self, **kwargs):
        self.request = kwargs
        return iter([
            SimpleNamespace(data=SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="Bon"))],
                usage=None,
            )),
            SimpleNamespace(data=SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="jour"))],
                usage=None,
            )),
            SimpleNamespace(data=SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
            )),
        ])


def test_mistral_finalize_stream_handles_sse_data_envelope() -> None:
    chat = _Chat()
    provider = MistralToolCallingProvider(client=SimpleNamespace(chat=chat))

    chunks = list(provider.finalize_stream([{"role": "user", "content": "hello"}], []))

    assert chunks == ["Bon", "jour"]
    assert chat.request["model"] == "mistral-large-latest"
    assert provider._last_stream_usage == {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
    assert provider.capabilities().supports_finalize_streaming is True
