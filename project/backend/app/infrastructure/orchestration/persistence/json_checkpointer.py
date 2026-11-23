"""LangGraph checkpointer mirroring atomic JSON snapshots without blocking async loops."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
)
from langgraph.checkpoint.memory import MemorySaver

from app.domain.graph_state import GraphState
from app.infrastructure.orchestration.persistence.hydration import (
    _default_log_path,
    can_resume,
    hydrate_resumed_state,
    load_graph_state,
    save_graph_state,
)
from app.infrastructure.orchestration.state.schema import CompanyState


class JsonStateCheckpointer(MemorySaver):
    """LangGraph checkpointer mirroring step snapshots to JSON and implementing the port."""

    def __init__(self, log_path: Path | str | None = None) -> None:
        super().__init__()
        self.log_path = Path(log_path) if log_path else _default_log_path()

    # -- LangGraph BaseCheckpointSaver hooks ------------------------------- #
    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        res = super().put(config, checkpoint, metadata, new_versions)
        self._write_snapshot(checkpoint)
        return res

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        res = await super().aput(config, checkpoint, metadata, new_versions)
        await asyncio.to_thread(self._write_snapshot, checkpoint)
        return res

    def _write_snapshot(self, checkpoint: Checkpoint) -> None:
        channel_values = checkpoint.get("channel_values", {})
        state_candidate = None
        if "company_state" in channel_values:
            state_candidate = channel_values["company_state"]
        elif channel_values and any(k in channel_values for k in ("project_id", "current_phase")):
            state_candidate = channel_values

        if isinstance(state_candidate, dict) and "project_id" in state_candidate:
            save_graph_state(cast(GraphState, state_candidate), self.log_path)

    # -- IOrchestratorCheckpointer port methods ---------------------------- #
    def save(self, state: GraphState, filepath: Path | str | None = None) -> Path:
        """Persist *state* to *filepath* or this checkpointer's configured ``log_path``."""
        target = filepath if filepath is not None else self.log_path
        return save_graph_state(state, target)

    def load(self, filepath: Path | str | None = None) -> CompanyState | None:
        """Load persisted state from *filepath* or this checkpointer's ``log_path``."""
        target = filepath if filepath is not None else self.log_path
        return load_graph_state(target)

    def can_resume(self, state: GraphState | None) -> bool:
        """Return True if *state* represents an incomplete execution."""
        return can_resume(state)

    def hydrate(self, state: GraphState) -> CompanyState:
        """Hydrate *state* to resume from the next incomplete phase."""
        return hydrate_resumed_state(state)


__all__ = ["JsonStateCheckpointer"]
