"""Domain error taxonomy.

Resilience decisions (retry / fallback) are driven by the two class-level flags on
:class:`ProviderError` -- never by matching message strings:

* ``retryable``          -- the same provider may be tried again (with backoff).
* ``fallback_eligible``  -- after retries are exhausted, routing to a fallback
                            provider is sensible. ``False`` means the failure is
                            about the *request* (auth, invalid input, budget) so
                            another provider would not help.

Adapters MUST translate native failures into these types.
"""

from __future__ import annotations

from typing import ClassVar


class VibeError(Exception):
    """Base class for every error raised intentionally by this code base."""


class ConfigurationError(VibeError):
    """Invalid or missing configuration."""


class ValidationError(VibeError):
    """A domain object was constructed with invalid values."""


class IllegalTransitionError(VibeError):
    """A task state transition that the state machine does not allow."""


class ProviderNotFoundError(VibeError):
    """No provider is registered under the requested id."""


class ErrorInfo:
    """Serializable, immutable description of a failure (stored on results/attempts)."""

    __slots__ = ("code", "message", "retry_after", "retryable")

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "retry_after": self.retry_after,
        }

    def __repr__(self) -> str:
        return f"ErrorInfo(code={self.code!r}, message={self.message!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ErrorInfo) and self.to_dict() == other.to_dict()

    def __hash__(self) -> int:
        return hash((self.code, self.message, self.retryable, self.retry_after))


class ProviderError(VibeError):
    """Base class for failures originating from an agent provider."""

    code: ClassVar[str] = "provider_error"
    retryable: ClassVar[bool] = False
    fallback_eligible: ClassVar[bool] = True

    def __init__(
        self,
        message: str = "",
        *,
        provider_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.provider_id = provider_id
        self.retry_after = retry_after

    def to_info(self) -> ErrorInfo:
        return ErrorInfo(
            self.code, self.message, retryable=self.retryable, retry_after=self.retry_after
        )


class RateLimitedError(ProviderError):
    """HTTP 429 / quota exhaustion. Honour ``retry_after`` when present."""

    code = "rate_limited"
    retryable = True


class ProviderTimeoutError(ProviderError):
    code = "timeout"
    retryable = True


class TransientProviderError(ProviderError):
    """5xx, connection reset, other failures that are likely to clear up."""

    code = "transient"
    retryable = True


class StreamDisconnectedError(ProviderError):
    """The stream ended abnormally (no ``done`` event / socket dropped)."""

    code = "stream_disconnected"
    retryable = True


class ProviderUnavailableError(ProviderError):
    """Provider is down / not installed / failed to load. Fail over immediately."""

    code = "provider_unavailable"
    retryable = False


class AuthenticationError(ProviderError):
    code = "authentication_failed"
    fallback_eligible = False


class InvalidRequestError(ProviderError):
    code = "invalid_request"
    fallback_eligible = False


class ContentRejectedError(ProviderError):
    code = "content_rejected"
    fallback_eligible = False


class BudgetExceededError(ProviderError):
    code = "budget_exceeded"
    fallback_eligible = False


class ExecutionCancelledError(ProviderError):
    """Raised by a stream that was stopped via ``cancel``. Never retried."""

    code = "cancelled"
    fallback_eligible = False
