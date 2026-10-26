"""Shared toolkit for provider authors (the ONLY shared code between providers).

Lives outside ``vibe_providers.adapters`` so the "providers are independent"
architecture contract can stay strict.
"""

from vibe_providers.kit.base import BaseAgentRunner, fold_events
from vibe_providers.kit.http_errors import (
    error_from_status,
    extract_error_message,
    parse_retry_after,
    translate_httpx_error,
)
from vibe_providers.kit.http_runner import (
    Delta,
    HttpRequest,
    HttpStreamingRunner,
    StreamState,
)
from vibe_providers.kit.sse import SSEMessage, iter_sse

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
