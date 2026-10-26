"""HTTP -> domain error translation shared by every HTTP-based provider.

Map by status code / exception TYPE, never by message text, so the resilience layer can rely on
the ``retryable`` / ``fallback_eligible`` flags of the domain taxonomy.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx
from app.domain.errors import (
    AuthenticationError,
    BudgetExceededError,
    InvalidRequestError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitedError,
    StreamDisconnectedError,
    TransientProviderError,
)

_MAX_MESSAGE = 300


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header expressed in seconds (HTTP-date form is ignored)."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def extract_error_message(body: bytes | str) -> str:
    """Pull ``error.message`` out of a JSON error body; fall back to a truncated raw body."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    try:
        payload = json.loads(text)
    except ValueError:
        return text.strip()[:_MAX_MESSAGE]
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping) and error.get("message"):
            return str(error["message"])[:_MAX_MESSAGE]
        if isinstance(error, str):
            return error[:_MAX_MESSAGE]
        if payload.get("message"):
            return str(payload["message"])[:_MAX_MESSAGE]
    return text.strip()[:_MAX_MESSAGE]


def error_from_status(
    status: int,
    message: str,
    *,
    provider_id: str,
    retry_after: float | None = None,
) -> ProviderError:
    """Translate an HTTP status code into the domain error taxonomy."""
    cls: type[ProviderError]
    if status in (401, 403):
        cls = AuthenticationError
    elif status == 402:
        cls = BudgetExceededError
    elif status == 429:
        cls = RateLimitedError
    elif status in (408, 504):
        cls = ProviderTimeoutError
    elif 400 <= status < 500:
        cls = InvalidRequestError
    elif status >= 500:  # includes Anthropic's 529 "overloaded"
        cls = TransientProviderError
    else:
        cls = ProviderError
    return cls(message or f"HTTP {status}", provider_id=provider_id, retry_after=retry_after)


def translate_httpx_error(exc: BaseException, *, provider_id: str) -> ProviderError:
    """Translate transport-level failures (timeouts, resets, DNS...)."""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError("request timed out", provider_id=provider_id)
    if isinstance(exc, (httpx.ReadError, httpx.RemoteProtocolError, httpx.StreamError)):
        return StreamDisconnectedError(
            f"stream interrupted: {type(exc).__name__}", provider_id=provider_id
        )
    if isinstance(exc, httpx.TransportError):
        return TransientProviderError(
            f"connection problem: {type(exc).__name__}", provider_id=provider_id
        )
    return ProviderError(f"unexpected {type(exc).__name__}", provider_id=provider_id)
