from __future__ import annotations

from typing import Protocol


class TaskQueue(Protocol):
    """FIFO of task ids awaiting execution. In-process now; swappable for Redis later."""

    async def put(self, task_id: str) -> None: ...

    async def get(self) -> str: ...

    def task_done(self) -> None: ...

    def qsize(self) -> int: ...
