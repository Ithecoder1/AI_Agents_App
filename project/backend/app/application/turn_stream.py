"""Replayable turn stream and turn execution handle representations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.domain.chat import ChatMessage, ChatSession
from app.domain.models import StreamEvent


class TurnStream:
    """Replayable in-memory event log for one running turn, with live subscription.

    Event ``seq`` values are 1-based and gap-free; ``subscribe(after_seq=N)`` resumes after the
    first ``N`` events (the WebSocket "cursor").
    """

    def __init__(self) -> None:
        self._events: list[StreamEvent] = []
        self._closed = False
        self._cond = asyncio.Condition()

    @property
    def finished(self) -> bool:
        return self._closed

    @property
    def events(self) -> tuple[StreamEvent, ...]:
        return tuple(self._events)

    async def publish(self, event: StreamEvent) -> StreamEvent:
        async with self._cond:
            stamped = event.with_seq(len(self._events) + 1)
            self._events.append(stamped)
            self._cond.notify_all()
        return stamped

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            self._cond.notify_all()

    async def subscribe(self, after_seq: int = 0) -> AsyncIterator[StreamEvent]:
        index = max(after_seq, 0)
        while True:
            async with self._cond:
                curr_idx = index

                def can_progress(idx: int = curr_idx) -> bool:
                    return idx < len(self._events) or self._closed

                await self._cond.wait_for(can_progress)
                batch = self._events[index:]
                finished = self._closed and not batch
            if finished:
                return
            for event in batch:
                index += 1
                yield event


@dataclass(frozen=True, slots=True)
class StartedTurn:
    session: ChatSession
    user_message: ChatMessage
    assistant_message: ChatMessage  # persisted placeholder, status "streaming"
    stream: TurnStream
