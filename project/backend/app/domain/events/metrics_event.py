"""MetricsEvent domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import EventType
from app.domain.events.stream_event import StreamEvent, utcnow
from app.domain.usage import Usage


@dataclass(frozen=True, slots=True)
class MetricsEvent:
    usage: Usage = field(default_factory=Usage)
    duration_ms: int = 0
    task_id: str = ""
    attempt: int = 1
    ts: datetime = field(default_factory=utcnow)

    def to_stream_event(self) -> StreamEvent:
        return StreamEvent(
            task_id=self.task_id,
            attempt=self.attempt,
            type=EventType.USAGE,
            data=self.usage.to_dict(),
            ts=self.ts,
        )
