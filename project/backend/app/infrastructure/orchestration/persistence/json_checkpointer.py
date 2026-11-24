"""Non-blocking JSON-backed LangGraph checkpointer delegating to GraphStateHydrator."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.memory import MemorySaver

from app.infrastructure.orchestration.persistence.hydration import GraphStateHydrator
from app.infrastructure.orchestration.state import GraphState


class JsonStateCheckpointer(MemorySaver):
    """LangGraph ``MemorySaver`` persisting atomic JSON snapshots via ``GraphStateHydrator``.

    Implements ``IOrchestratorCheckpointer`` and offloads async disk I/O in ``aput``
    through ``asyncio.to_thread`` to keep the asyncio event loop non-blocking.
    """

    def __init__(
        self,
        log_path: Path | str | None = None,
        *,
        hydrator: GraphStateHydrator | None = None,
    ) -> None:
        super().__init__()
        self._hydrator = hydrator if hydrator is not None else GraphStateHydrator()
        self.log_path: Path = (
            Path(log_path) if log_path is not None else self._hydrator.default_log_path()
        )

    @property
    def hydrator(self) -> GraphStateHydrator:
        """Return the ``GraphStateHydrator`` used for serialization and resumption."""
        return self._hydrator

    def _write_snapshot(self, checkpoint: Checkpoint) -> None:
        channel_values = checkpoint.get("channel_values")
        if isinstance(channel_values, Mapping) and "project_id" in channel_values:
            self._hydrator.save(cast("GraphState", dict(channel_values)), self.log_path)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Persist checkpoint in memory and flush JSON snapshot synchronously."""
        updated = super().put(config, checkpoint, metadata, new_versions)
        self._write_snapshot(checkpoint)
        return updated

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Persist checkpoint in memory and offload JSON snapshot writes to a thread pool."""
        updated = await super().aput(config, checkpoint, metadata, new_versions)
        await asyncio.to_thread(self._write_snapshot, checkpoint)
        return updated

    def save(self, state: GraphState, filepath: Path | str | None = None) -> Path:
        """Save ``state`` to ``filepath`` (or ``self.log_path``) atomically."""
        target = filepath if filepath is not None else self.log_path
        return self._hydrator.save(state, target)

    def load(self, filepath: Path | str | None = None) -> GraphState | None:
        """Load ``GraphState`` from ``filepath`` (or ``self.log_path``)."""
        target = filepath if filepath is not None else self.log_path
        return self._hydrator.load(target)

    def can_resume(self, state: GraphState | None) -> bool:
        """Delegate resumability evaluation to the configured hydrator."""
        return self._hydrator.can_resume(state)

    def hydrate(self, state: GraphState) -> GraphState:
        """Delegate state resumption hydration to the configured hydrator."""
        return self._hydrator.hydrate(state)


__all__ = ["JsonStateCheckpointer"]
