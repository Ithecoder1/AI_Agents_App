"""ToolCallEvent domain model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.enums import EventType
from app.domain.events.stream_event import StreamEvent, utcnow


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    name: str
    args: Mapping[str, Any] = field(default_factory=dict)
    task_id: str = ""
    attempt: int = 1
    ts: datetime = field(default_factory=utcnow)

    def to_stream_event(self) -> StreamEvent:
        return StreamEvent(
            task_id=self.task_id,
            attempt=self.attempt,
            type=EventType.TOOL_CALL,
            data={"name": self.name, "args": dict(self.args)},
            ts=self.ts,
        )
