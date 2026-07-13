from __future__ import annotations

from types import SimpleNamespace

import pytest

from mtp import ProviderError, ProviderErrorCategory, normalize_provider_error
from mtp.providers.groq_provider import GroqToolCallingProvider


class _SDKError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, headers=None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, headers=headers or {})


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (401, ProviderErrorCategory.AUTHENTICATION, False),
        (403, ProviderErrorCategory.PERMISSION, False),
        (400, ProviderErrorCategory.INVALID_REQUEST, False),
        (429, ProviderErrorCategory.RATE_LIMIT, True),
        (503, ProviderErrorCategory.SERVER, True),
    ],
)
def test_normalize_provider_error_by_status(status, category, retryable):
    error = normalize_provider_error(_SDKError("unsafe" , status_code=status), provider="test")

    assert isinstance(error, RuntimeError)
    assert error.category is category
    assert error.retryable is retryable
    assert error.status_code == status


def test_normalization_never_copies_secrets_or_response_bodies():
    secret = "gsk_super_secret_credential"
    source = _SDKError(
        f"Authorization: Bearer {secret}; prompt=private customer data",
        status_code=429,
        headers={
            "retry-after": "2.5",
            "x-request-id": "req-safe_123",
            "authorization": f"Bearer {secret}",
        },
    )

    error = normalize_provider_error(source, provider="groq")
    rendered = str(error)
    serialized = repr(error.to_dict())

    assert secret not in rendered + serialized
    assert "private customer data" not in rendered + serialized
    assert error.retry_after_seconds == 2.5
    assert error.request_id == "req-safe_123"
    assert error.to_dict()["category"] == "rate_limit"


def test_timeout_and_connection_class_names_are_normalized():
    class APIConnectionError(Exception):
        pass

    assert normalize_provider_error(TimeoutError(), provider="p").category is ProviderErrorCategory.TIMEOUT
    assert (
        normalize_provider_error(APIConnectionError(), provider="p").category
        is ProviderErrorCategory.CONNECTION
    )


def test_existing_provider_error_is_idempotent():
    original = normalize_provider_error(TimeoutError(), provider="groq")
    assert normalize_provider_error(original, provider="other") is original


def test_unsafe_diagnostic_identifiers_are_dropped():
    source = _SDKError(
        "unsafe",
        status_code=500,
        headers={"x-request-id": "request-id Authorization: secret", "retry-after": "999999"},
    )
    error = normalize_provider_error(source, provider="bad provider\nsecret")

    assert error.provider == "unknown"
    assert error.request_id is None
    assert error.retry_after_seconds is None


class _Completions:
    def __init__(self, exc: Exception, *, fail_first_with_type_error: bool = False) -> None:
        self.exc = exc
        self.fail_first_with_type_error = fail_first_with_type_error
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.fail_first_with_type_error and self.calls == 1:
            raise TypeError("old client")
        raise self.exc


def _groq_provider(completions: _Completions) -> GroqToolCallingProvider:
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return GroqToolCallingProvider(client=client)


@pytest.mark.parametrize("fallback", [False, True])
def test_groq_request_failures_use_shared_safe_taxonomy(fallback):
    secret = "gsk_do_not_log"
    completions = _Completions(
        _SDKError(f"Bearer {secret}", status_code=401),
        fail_first_with_type_error=fallback,
    )
    provider = _groq_provider(completions)

    with pytest.raises(ProviderError) as caught:
        provider._create_completion({"parallel_tool_calls": True})

    assert caught.value.category is ProviderErrorCategory.AUTHENTICATION
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None


def test_groq_malformed_response_is_invalid_response_error():
    with pytest.raises(ProviderError) as caught:
        GroqToolCallingProvider._first_choice_message(SimpleNamespace(choices=[]))

    assert caught.value.category is ProviderErrorCategory.INVALID_RESPONSE
    assert isinstance(caught.value, RuntimeError)


def test_groq_stream_iteration_failures_are_normalized():
    secret = "gsk_stream_secret"

    def failing_stream():
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="partial"))],
            usage=None,
        )
        raise _SDKError(f"Bearer {secret}", status_code=503)

    class _StreamingCompletions:
        @staticmethod
        def create(**kwargs):
            return failing_stream()

    provider = _groq_provider(_StreamingCompletions())
    stream = provider.finalize_stream([{"role": "user", "content": "hi"}], [])

    assert next(stream) == "partial"
    with pytest.raises(ProviderError) as caught:
        next(stream)
    assert caught.value.category is ProviderErrorCategory.SERVER
    assert caught.value.retryable is True
    assert secret not in str(caught.value)
