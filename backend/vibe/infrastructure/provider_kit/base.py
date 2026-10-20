"""``BaseAgentRunner``: template-method base class for provider adapters.

A simple adapter only has to implement :meth:`stream_execute` and :meth:`health_check`.
``execute`` is derived by folding the stream into an :class:`ExecutionResult`.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from vibe.domain.enums import EventType, TaskStatus
from vibe.domain.errors import StreamDisconnectedError
from vibe.domain.models import (
    AgentProviderConfig,
    Artifact,
    ExecutionResult,
    ExecutionTask,
    HealthStatus,
    ProviderCapabilities,
    StreamEvent,
    Usage,
)
from vibe.domain.ports.agent_provider import ProviderDeps


async def fold_events(
    task: ExecutionTask,
    provider_id: str,
    events: AsyncIterator[StreamEvent],
    *,
    started_at: float | None = None,
) -> ExecutionResult:
    """Collapse a provider stream into a result.

    Raises :class:`StreamDisconnectedError` if the stream ends without a ``DONE`` event.
    Provider errors propagate untouched.
    """
    t0 = started_at if started_at is not None else time.monotonic()
    text: list[str] = []
    artifacts: list[Artifact] = []
    usage = Usage()
    done = False
    async for event in events:
        if event.type is EventType.TOKEN:
            text.append(str(event.data.get("text", "")))
        elif event.type is EventType.ARTIFACT:
            artifacts.append(Artifact.from_dict(event.data))
        elif event.type is EventType.USAGE:
            usage = usage + Usage.from_dict(event.data)
        elif event.type is EventType.DONE:
            done = True
            break
    if not done:
        raise StreamDisconnectedError(
            "stream ended without a 'done' event", provider_id=provider_id
        )
    return ExecutionResult(
        task_id=task.id,
        status=TaskStatus.SUCCEEDED,
        provider_id=provider_id,
        output_text="".join(text),
        artifacts=tuple(artifacts),
        usage=usage,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


class BaseAgentRunner(ABC):
    """Base class implementing everything in ``IAgentProvider`` that is boilerplate."""

    #: Honest defaults for the adapter; operators may override via config ``capabilities:``.
    default_capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities()

    def __init__(self, config: AgentProviderConfig, deps: ProviderDeps) -> None:
        self.config = config
        self.deps = deps
        self._capabilities = type(self).default_capabilities.merged(config.capabilities)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    @abstractmethod
    def stream_execute(self, task: ExecutionTask) -> AsyncIterator[StreamEvent]:
        """Yield events (``seq=0``) ending with one ``DONE``; raise ``ProviderError`` on failure.

        Implement as an ``async def`` generator.
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Never raise; report problems as ``HealthStatus.down(reason)``."""
        raise NotImplementedError

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        return await fold_events(task, self.config.id, self.stream_execute(task))

    async def cancel(self, task_id: str) -> None:
        """Default: nothing to cancel. Override together with ``supports_cancel=True``."""
        return None

    async def aclose(self) -> None:
        """Release clients/processes. Default: nothing to release."""
        return None

    # -- helpers for subclasses ------------------------------------------- #
    def event(self, task: ExecutionTask, type_: EventType, **data: Any) -> StreamEvent:
        return StreamEvent.create(task.id, task.attempt, type_, **data)

    def secret(self, name: str) -> str | None:
        return self.deps.secrets.get(name)
