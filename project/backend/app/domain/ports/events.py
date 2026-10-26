from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from vibe.domain.models import StreamEvent


class EventStore(Protocol):
    """Durable, ordered per-task event log. Backs resumable streaming (``after_seq``)."""

    async def append(self, event: StreamEvent) -> StreamEvent:
        """Persist *event*, assigning the next per-task ``seq``; returns the stored event."""
        ...

    async def read(self, task_id: str, *, after_seq: int = 0) -> Sequence[StreamEvent]: ...


class EventBus(Protocol):
    """Live fan-out of already-persisted events to subscribers."""

    async def publish(self, event: StreamEvent) -> None: ...

    def subscribe(self, task_id: str) -> AbstractAsyncContextManager[AsyncIterator[StreamEvent]]:
        """``async with bus.subscribe(id) as events: async for e in events: ...``"""
        ...
