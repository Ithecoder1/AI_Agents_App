"""Buffered persistence of a streaming assistant message.

Committing to SQLite on every token chunk would hammer the database (and the WAL), so progress is
flushed only on structural boundary events or when ~``interval_s`` has elapsed since the last
flush. The final state is always written by ``ChatRepository.finalize_message``; flushes exist so
that a crash mid-stream loses at most the last interval, and so a page reload shows progress.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from app.application.stream_aggregator import MessageStreamAggregator
from app.domain.chat import STATUS_STREAMING
from app.domain.enums import EventType
from app.domain.ports.chat import ChatRepository

_log = logging.getLogger(__name__)

# Events that change nothing the message row stores.
_NOT_PERSISTED = frozenset({EventType.STATUS, EventType.USAGE})


class BufferedMessageWriter:
    def __init__(
        self,
        repository: ChatRepository,
        message_id: str,
        aggregator: MessageStreamAggregator,
        *,
        interval_s: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repo = repository
        self._message_id = message_id
        self._aggregator = aggregator
        self._interval_s = interval_s
        self._clock = clock
        self._dirty = False
        self._last_flush = clock()
        self.flush_count = 0

    async def after_event(self, event_type: EventType, boundary: bool) -> None:
        """Call once per applied event; flushes when due."""
        if event_type not in _NOT_PERSISTED:
            self._dirty = True
        if self._dirty and (boundary or self._clock() - self._last_flush >= self._interval_s):
            await self.flush()

    async def flush(self) -> None:
        if not self._dirty:
            return
        try:
            await self._repo.save_message_progress(
                self._message_id,
                content=self._aggregator.content,
                metadata=self._aggregator.metadata(STATUS_STREAMING),
            )
        except Exception:
            # A transient DB hiccup must not kill the live stream; the final write retries
            # with the complete state.
            _log.warning("progress flush failed for message %s", self._message_id, exc_info=True)
            return
        self._dirty = False
        self._last_flush = self._clock()
        self.flush_count += 1
