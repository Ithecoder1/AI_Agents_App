"""The provider contract every agent back-end implements."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from vibe.domain.models import (
    AgentProviderConfig,
    ExecutionResult,
    ExecutionTask,
    HealthStatus,
    ProviderCapabilities,
    StreamEvent,
)
from vibe.domain.ports.secrets import SecretResolver


@runtime_checkable
class IAgentProvider(Protocol):
    """Adapter contract for an agent infrastructure (OpenRouter, Antigravity, packages...).

    Behavioural rules (verified by ``tests/contract/provider_contract.py``):

    * ``stream_execute`` yields events with ``seq=0`` and ends with exactly one ``DONE``
      event on success. On failure it RAISES a ``ProviderError`` subclass -- it never
      swallows errors into the stream and never ends silently.
    * ``execute`` returns an ``ExecutionResult`` on success and raises on failure.
    * ``cancel`` is idempotent, safe for unknown task ids, and never raises. A cancelled
      stream terminates by raising ``ExecutionCancelledError``.
    * ``health_check`` never raises; failures are reported as ``HealthStatus.down``.
    * Secrets must never appear in events, logs, or exception messages.
    """

    config: AgentProviderConfig

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def execute(self, task: ExecutionTask) -> ExecutionResult: ...

    def stream_execute(self, task: ExecutionTask) -> AsyncIterator[StreamEvent]: ...

    async def health_check(self) -> HealthStatus: ...

    async def cancel(self, task_id: str) -> None: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderDeps:
    """Runtime collaborators handed to every provider factory."""

    secrets: SecretResolver
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("vibe.provider"))


# A provider factory is any callable ``(config, deps) -> IAgentProvider``: a class whose
# constructor has that signature (e.g. a BaseAgentRunner subclass) or a plain function.
ProviderFactory = Callable[[AgentProviderConfig, ProviderDeps], IAgentProvider]
