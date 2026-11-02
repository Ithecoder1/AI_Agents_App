"""TextDeltaEvent domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import EventType
from app.domain.events.stream_event import StreamEvent, utcnow


@dataclass(frozen=True, slots=True)
class TextDeltaEvent:
    text: str
    task_id: str = ""
    attempt: int = 1
    ts: datetime = field(default_factory=utcnow)

    def to_stream_event(self) -> StreamEvent:
        return StreamEvent(
            task_id=self.task_id,
            attempt=self.attempt,
            type=EventType.TOKEN,
            data={"text": self.text},
            ts=self.ts,
        )
