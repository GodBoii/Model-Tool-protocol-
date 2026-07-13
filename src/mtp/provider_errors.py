from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, NoReturn


class ProviderErrorCategory(str, Enum):
    """Stable, provider-independent categories for remote model failures."""

    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    SERVER = "server"
    UNKNOWN = "unknown"


_RETRYABLE_CATEGORIES = {
    ProviderErrorCategory.RATE_LIMIT,
    ProviderErrorCategory.TIMEOUT,
    ProviderErrorCategory.CONNECTION,
    ProviderErrorCategory.SERVER,
}


@dataclass(frozen=True, slots=True)
class ProviderErrorDetails:
    """Structured error metadata safe to log or serialize."""

    provider: str
    category: ProviderErrorCategory
    retryable: bool
    status_code: int | None = None
    request_id: str | None = None
    retry_after_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self.provider,
            "category": self.category.value,
            "retryable": self.retryable,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        return payload


class ProviderError(RuntimeError):
    """A safe, normalized provider failure.

    This remains a :class:`RuntimeError` for compatibility with applications
    that handled MTP's previous provider request wrappers.  The original
    exception is intentionally not retained: SDK exception messages and HTTP
    bodies can contain credentials or user prompt data.
    """

    def __init__(self, details: ProviderErrorDetails) -> None:
        self.details = details
        self.provider = details.provider
        self.category = details.category
        self.retryable = details.retryable
        self.status_code = details.status_code
        self.request_id = details.request_id
        self.retry_after_seconds = details.retry_after_seconds
        super().__init__(_safe_error_message(details))

    def to_dict(self) -> dict[str, Any]:
        return self.details.to_dict()


def normalize_provider_error(
    exc: BaseException,
    *,
    provider: str,
    category: ProviderErrorCategory | str | None = None,
) -> ProviderError:
    """Convert an arbitrary provider SDK exception to a safe common error.

    Classification uses status codes and common SDK exception class names, so
    importing every optional provider dependency is unnecessary.  No source
    exception text, response body, headers, or credentials are copied.
    """

    if isinstance(exc, ProviderError):
        return exc

    normalized_provider = _safe_identifier(provider, fallback="unknown")
    status_code = _status_code(exc)
    normalized_category = _coerce_category(category) or _classify(exc, status_code)
    retry_after = _retry_after_seconds(exc)
    request_id = _request_id(exc)
    details = ProviderErrorDetails(
        provider=normalized_provider,
        category=normalized_category,
        retryable=normalized_category in _RETRYABLE_CATEGORIES,
        status_code=status_code,
        request_id=request_id,
        retry_after_seconds=retry_after,
    )
    return ProviderError(details)


def raise_normalized_provider_error(
    exc: BaseException,
    *,
    provider: str,
    category: ProviderErrorCategory | str | None = None,
) -> NoReturn:
    """Raise a normalized error without an unsafe exception-chain traceback."""

    raise normalize_provider_error(exc, provider=provider, category=category) from None


def _coerce_category(value: ProviderErrorCategory | str | None) -> ProviderErrorCategory | None:
    if value is None:
        return None
    if isinstance(value, ProviderErrorCategory):
        return value
    return ProviderErrorCategory(value)


def _classify(exc: BaseException, status_code: int | None) -> ProviderErrorCategory:
    if status_code in (401,):
        return ProviderErrorCategory.AUTHENTICATION
    if status_code in (403,):
        return ProviderErrorCategory.PERMISSION
    if status_code == 429:
        return ProviderErrorCategory.RATE_LIMIT
    if status_code is not None and 500 <= status_code <= 599:
        return ProviderErrorCategory.SERVER
    if status_code is not None and 400 <= status_code <= 499:
        return ProviderErrorCategory.INVALID_REQUEST

    name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in name or "timedout" in name:
        return ProviderErrorCategory.TIMEOUT
    if any(token in name for token in ("authentication", "unauthorized", "invalidtoken")):
        return ProviderErrorCategory.AUTHENTICATION
    if any(token in name for token in ("permission", "forbidden")):
        return ProviderErrorCategory.PERMISSION
    if "ratelimit" in name or "too_many_requests" in name:
        return ProviderErrorCategory.RATE_LIMIT
    if any(token in name for token in ("connection", "connecterror", "networkerror")):
        return ProviderErrorCategory.CONNECTION
    if any(token in name for token in ("badrequest", "invalidrequest", "unprocessable")):
        return ProviderErrorCategory.INVALID_REQUEST
    if any(token in name for token in ("internalserver", "servererror", "serviceunavailable")):
        return ProviderErrorCategory.SERVER
    return ProviderErrorCategory.UNKNOWN


def _status_code(exc: BaseException) -> int | None:
    values = [getattr(exc, "status_code", None), getattr(exc, "status", None)]
    response = getattr(exc, "response", None)
    if response is not None:
        values.extend((getattr(response, "status_code", None), getattr(response, "status", None)))
    for value in values:
        try:
            code = int(value)
        except (TypeError, ValueError):
            continue
        if 100 <= code <= 599:
            return code
    return None


def _response_headers(exc: BaseException) -> Any:
    response = getattr(exc, "response", None)
    return getattr(response, "headers", None) if response is not None else None


def _header(headers: Any, name: str) -> Any:
    if headers is None:
        return None
    try:
        return headers.get(name) or headers.get(name.lower())
    except (AttributeError, TypeError):
        return None


def _request_id(exc: BaseException) -> str | None:
    value = getattr(exc, "request_id", None) or _header(_response_headers(exc), "x-request-id")
    if value is None:
        return None
    return _safe_identifier(str(value), fallback=None)


def _retry_after_seconds(exc: BaseException) -> float | None:
    value = getattr(exc, "retry_after", None)
    if value is None:
        value = _header(_response_headers(exc), "retry-after")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0 or seconds > 86_400:
        return None
    return seconds


def _safe_identifier(value: str, *, fallback: str | None) -> str | None:
    # Request IDs and provider names should never need whitespace, delimiters,
    # or arbitrary response text. Restricting this alphabet prevents accidental
    # secret/body propagation through otherwise useful diagnostic metadata.
    stripped = value.strip()[:128]
    if not stripped or re.fullmatch(r"[A-Za-z0-9._:/-]+", stripped) is None:
        return fallback
    return stripped


def _safe_error_message(details: ProviderErrorDetails) -> str:
    labels = {
        ProviderErrorCategory.AUTHENTICATION: "authentication failed",
        ProviderErrorCategory.PERMISSION: "request was not permitted",
        ProviderErrorCategory.RATE_LIMIT: "request was rate limited",
        ProviderErrorCategory.TIMEOUT: "request timed out",
        ProviderErrorCategory.CONNECTION: "could not connect",
        ProviderErrorCategory.INVALID_REQUEST: "request was rejected",
        ProviderErrorCategory.INVALID_RESPONSE: "returned an invalid response",
        ProviderErrorCategory.SERVER: "service failed",
        ProviderErrorCategory.UNKNOWN: "request failed",
    }
    context: list[str] = []
    if details.status_code is not None:
        context.append(f"HTTP {details.status_code}")
    if details.request_id is not None:
        context.append(f"request {details.request_id}")
    if details.retry_after_seconds is not None:
        context.append(f"retry after {details.retry_after_seconds:g}s")
    suffix = f" ({'; '.join(context)})" if context else ""
    return f"{details.provider} {labels[details.category]}{suffix}."
