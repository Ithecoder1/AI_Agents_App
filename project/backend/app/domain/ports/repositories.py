from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from vibe.domain.enums import TaskStatus
from vibe.domain.models import ExecutionTask, Prompt


class TaskRepository(Protocol):
    async def add(self, task: ExecutionTask) -> None: ...

    async def get(self, task_id: str) -> ExecutionTask | None: ...

    async def update(self, task: ExecutionTask) -> None: ...

    async def get_by_idempotency_key(self, key: str) -> ExecutionTask | None: ...

    async def list(
        self,
        *,
        status: TaskStatus | None = None,
        provider_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[ExecutionTask]: ...

    async def list_unfinished(self) -> Sequence[ExecutionTask]:
        """Tasks left QUEUED/RUNNING/RETRYING -- used for crash recovery at startup."""
        ...


class PromptRepository(Protocol):
    async def save(self, prompt: Prompt) -> None: ...

    async def get(self, prompt_id: str) -> Prompt | None: ...

    async def list(self, *, limit: int = 50, offset: int = 0) -> Sequence[Prompt]: ...

    async def delete(self, prompt_id: str) -> None: ...
