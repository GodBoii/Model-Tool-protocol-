from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from mtp.providers.cohere_provider import CohereToolCallingProvider
from mtp.providers.deepseek_provider import DeepSeekToolCallingProvider
from mtp.providers.groq_provider import GroqToolCallingProvider
from mtp.providers.lmstudio_provider import LMStudioToolCallingProvider
from mtp.providers.mistral_provider import MistralToolCallingProvider
from mtp.providers.ollama_provider import OllamaToolCallingProvider
from mtp.providers.sambanova_provider import SambaNovaToolCallingProvider
from mtp.providers.xiaomi_provider import XiaomiToolCallingProvider


PROVIDERS = (
    MistralToolCallingProvider,
    CohereToolCallingProvider,
    DeepSeekToolCallingProvider,
    SambaNovaToolCallingProvider,
    XiaomiToolCallingProvider,
    OllamaToolCallingProvider,
    LMStudioToolCallingProvider,
    GroqToolCallingProvider,
)


@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_provider_timeouts_must_be_finite_and_positive(provider_type: type[Any], timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        provider_type(client=object(), timeout_seconds=timeout)


@pytest.mark.parametrize(
    ("provider_type", "field"),
    (
        (MistralToolCallingProvider, "max_tokens"),
        (CohereToolCallingProvider, "max_tokens"),
        (DeepSeekToolCallingProvider, "max_tokens"),
        (SambaNovaToolCallingProvider, "max_tokens"),
        (XiaomiToolCallingProvider, "max_tokens"),
        (LMStudioToolCallingProvider, "max_tokens"),
        (GroqToolCallingProvider, "max_completion_tokens"),
    ),
)
@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_provider_output_limits_must_be_positive_integers(
    provider_type: type[Any], field: str, limit: Any
) -> None:
    with pytest.raises(ValueError, match=field):
        provider_type(client=object(), **{field: limit})


def test_output_limits_reach_provider_request_arguments() -> None:
    mistral = MistralToolCallingProvider(client=object(), max_tokens=101)
    deepseek = DeepSeekToolCallingProvider(client=object(), max_tokens=102)
    sambanova = SambaNovaToolCallingProvider(client=object(), max_tokens=103)
    xiaomi = XiaomiToolCallingProvider(client=object(), max_tokens=104)
    lmstudio = LMStudioToolCallingProvider(client=object(), max_tokens=105)
    groq = GroqToolCallingProvider(client=object(), max_completion_tokens=106)
    cohere = CohereToolCallingProvider(client=object(), max_tokens=107)

    assert mistral._planning_args([], [])["max_tokens"] == 101
    assert deepseek._generation_args()["max_tokens"] == 102
    assert sambanova._generation_args()["max_tokens"] == 103
    assert xiaomi._request_args([])["max_tokens"] == 104
    assert lmstudio._generation_args()["max_tokens"] == 105
    assert groq._request_defaults()["max_completion_tokens"] == 106
    assert cohere._finalize_request([])["max_tokens"] == 107


class _ConstructorRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace())


def test_openai_compatible_clients_receive_same_sync_and_async_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    sync = _ConstructorRecorder()
    async_ = _ConstructorRecorder()
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=sync, AsyncOpenAI=async_),
    )

    DeepSeekToolCallingProvider(api_key="test", timeout_seconds=12.5)
    SambaNovaToolCallingProvider(api_key="test", timeout_seconds=13.5)
    xiaomi = XiaomiToolCallingProvider(api_key="test", timeout_seconds=14.5)
    xiaomi._get_async_client()
    lmstudio = LMStudioToolCallingProvider(api_key="test", timeout_seconds=15.5)
    lmstudio._get_async_client()

    assert [call["timeout"] for call in sync.calls] == [12.5, 13.5, 14.5, 15.5]
    assert [call["timeout"] for call in async_.calls] == [12.5, 13.5, 14.5, 15.5]


def test_native_sdk_clients_receive_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    mistral = _ConstructorRecorder()
    groq_sync = _ConstructorRecorder()
    groq_async = _ConstructorRecorder()
    cohere_sync = _ConstructorRecorder()
    cohere_async = _ConstructorRecorder()
    ollama_sync = _ConstructorRecorder()
    ollama_async = _ConstructorRecorder()
    monkeypatch.setitem(sys.modules, "mistralai", SimpleNamespace(Mistral=mistral))
    monkeypatch.setitem(
        sys.modules, "groq", SimpleNamespace(Groq=groq_sync, AsyncGroq=groq_async)
    )
    monkeypatch.setitem(
        sys.modules,
        "cohere",
        SimpleNamespace(ClientV2=cohere_sync, AsyncClientV2=cohere_async),
    )
    monkeypatch.setitem(
        sys.modules,
        "ollama",
        SimpleNamespace(Client=ollama_sync, AsyncClient=ollama_async),
    )

    MistralToolCallingProvider(api_key="test", timeout_seconds=16.5)
    GroqToolCallingProvider(api_key="test", timeout_seconds=17.5)
    cohere = CohereToolCallingProvider(api_key="test", timeout_seconds=18.5)
    cohere._get_async_client()
    ollama = OllamaToolCallingProvider(timeout_seconds=19.5)
    ollama._get_async_client()

    assert mistral.calls[0]["timeout_ms"] == 16_500
    assert groq_sync.calls[0]["timeout"] == 17.5
    assert groq_async.calls[0]["timeout"] == 17.5
    assert cohere_sync.calls[0]["timeout"] == 18.5
    assert cohere_async.calls[0]["timeout"] == 18.5
    assert ollama_sync.calls[0]["timeout"] == 19.5
    assert ollama_async.calls[0]["timeout"] == 19.5
