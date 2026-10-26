"""Shared toolkit for provider authors (the ONLY shared code between providers).

Lives outside ``providers.adapters`` so the "providers are independent"
architecture contract can stay strict.
"""

from providers.kit.base import BaseAgentRunner, fold_events
from providers.kit.http_errors import (
    error_from_status,
    extract_error_message,
    parse_retry_after,
    translate_httpx_error,
)
from providers.kit.http_runner import (
    Delta,
    HttpRequest,
    HttpStreamingRunner,
    StreamState,
)
from providers.kit.sse import SSEMessage, iter_sse

__all__ = [
    "BaseAgentRunner",
    "Delta",
    "HttpRequest",
    "HttpStreamingRunner",
    "SSEMessage",
    "StreamState",
    "error_from_status",
    "extract_error_message",
    "fold_events",
    "iter_sse",
    "parse_retry_after",
    "translate_httpx_error",
]
