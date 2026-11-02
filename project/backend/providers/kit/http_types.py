"""HTTP Streaming Runner types and state objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import EventType
from app.domain.models import Usage

#: ``(EventType.THOUGHT | EventType.TOKEN, text)``
Delta = tuple[EventType, str]


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """HTTP request specification built by an adapter."""

    path: str
    json: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Mapping[str, str] = field(default_factory=dict)
    method: str = "POST"


@dataclass(slots=True)
class StreamState:
    """Scratch space a ``parse_chunk`` implementation updates while a stream is consumed."""

    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None

    @property
    def has_usage(self) -> bool:
        return bool(self.prompt_tokens or self.completion_tokens or self.cost_usd)

    def to_usage(self) -> Usage:
        return Usage(self.prompt_tokens, self.completion_tokens, self.cost_usd)
