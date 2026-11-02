"""StreamEvent domain model.

One observable occurrence during execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.domain.enums import EventType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One observable occurrence during execution.

    Providers emit events with ``seq=0``; the application's event publisher assigns the
    monotonically increasing, persisted ``seq`` used for resumable streaming.

    ``data`` conventions per type:
      token/thought: ``{"text": str}``        status: ``{"state": str, ...}``
      log:           ``{"text": str, "stream": "stdout"|"stderr"}``
      tool_call:     ``{"name": str, "args": dict}``   tool_result: ``{"name": str, "result": Any}``
      artifact:      ``Artifact.to_dict()``   usage: ``Usage.to_dict()``
      error:         ``ErrorInfo.to_dict()``  done: ``{"finish_reason": str}``
    """

    task_id: str
    attempt: int
    type: EventType
    data: Mapping[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: datetime = field(default_factory=utcnow)

    @classmethod
    def create(cls, task_id: str, attempt: int, type: EventType, **data: Any) -> StreamEvent:
        return cls(task_id=task_id, attempt=attempt, type=type, data=data)

    def with_seq(self, seq: int) -> StreamEvent:
        return replace(self, seq=seq)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempt": self.attempt,
            "type": self.type.value,
            "data": dict(self.data),
            "seq": self.seq,
            "ts": self.ts.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> StreamEvent:
        ts = raw.get("ts")
        return cls(
            task_id=str(raw["task_id"]),
            attempt=int(raw.get("attempt", 1)),
            type=EventType(raw["type"]),
            data=dict(raw.get("data") or {}),
            seq=int(raw.get("seq", 0)),
            ts=datetime.fromisoformat(ts) if isinstance(ts, str) else utcnow(),
        )
