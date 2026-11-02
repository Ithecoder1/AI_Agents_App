"""Persistence & Resumability engine for the Autonomous Startup Orchestrator."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
)
from langgraph.checkpoint.memory import MemorySaver

from company_core.state import (
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DEVOPS,
    PHASE_QA,
    CompanyState,
    append_execution_log,
)


def _default_log_path() -> Path:
    return Path.cwd() / "logs" / "company_state.json"


def save_company_state(state: CompanyState, filepath: Path | str | None = None) -> Path:
    """Atomically persist CompanyState to JSON file."""
    path = Path(filepath) if filepath else _default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize with nice formatting
    payload = json.dumps(state, indent=2, ensure_ascii=False)

    # Atomic write via temp file + replace
    temp_dir = path.parent
    with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
        tf.write(payload)
        temp_name = tf.name

    os.replace(temp_name, path)
    return path


def load_company_state(filepath: Path | str | None = None) -> CompanyState | None:
    """Load previously persisted CompanyState from JSON file if it exists."""
    path = Path(filepath) if filepath else _default_log_path()
    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        # Validate required keys
        required = (
            "project_id",
            "user_requirement",
            "current_phase",
            "code_files",
            "completed_tasks",
            "retry_count",
        )
        if not all(k in data for k in required):
            return None
        return CompanyState(
            project_id=data["project_id"],
            user_requirement=data["user_requirement"],
            current_phase=data["current_phase"],
            architecture_spec=data.get("architecture_spec"),
            code_files=dict(data.get("code_files") or {}),
            test_results=data.get("test_results"),
            execution_logs=list(data.get("execution_logs") or []),
            completed_tasks=list(data.get("completed_tasks") or []),
            failed_tasks=list(data.get("failed_tasks") or []),
            error_message=data.get("error_message"),
            retry_count=int(data.get("retry_count") or 0),
        )
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
        return None


def can_resume(state: CompanyState | None) -> bool:
    """Check if state contains an interrupted or incomplete execution."""
    if state is None:
        return False
    return state.get("current_phase") != PHASE_COMPLETED


def determine_next_incomplete_phase(state: CompanyState) -> str:
    """Determine the next operational phase based on completed tasks."""
    completed = set(state.get("completed_tasks") or [])
    if "architecture" not in completed or not state.get("architecture_spec"):
        return PHASE_ARCHITECT
    if "code" not in completed:
        return PHASE_CODER
    if "devops" not in completed:
        return PHASE_DEVOPS
    if "qa" not in completed:
        return PHASE_QA
    return PHASE_COMPLETED


def hydrate_resumed_state(state: CompanyState) -> CompanyState:
    """Hydrate state to resume strictly from the last incomplete node, skipping completed tasks."""
    next_phase = determine_next_incomplete_phase(state)
    updated_logs = append_execution_log(
        state.get("execution_logs") or [],
        agent="system",
        phase=next_phase,
        message=(
            f"Resuming execution from incomplete phase: {next_phase}. "
            f"Skipping completed: {state.get('completed_tasks')}"
        ),
    )
    resumed = CompanyState(
        project_id=state["project_id"],
        user_requirement=state["user_requirement"],
        current_phase=next_phase,
        architecture_spec=state.get("architecture_spec"),
        code_files=dict(state.get("code_files") or {}),
        test_results=state.get("test_results"),
        execution_logs=updated_logs,
        completed_tasks=list(state.get("completed_tasks") or []),
        failed_tasks=list(state.get("failed_tasks") or []),
        error_message=state.get("error_message"),
        retry_count=state.get("retry_count", 0),
    )
    return resumed


class JsonStateCheckpointer(MemorySaver):
    """LangGraph Checkpointer that mirrors state checkpoints to json after every step."""

    def __init__(self, log_path: Path | str | None = None) -> None:
        super().__init__()
        self.log_path = Path(log_path) if log_path else _default_log_path()

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
        self._write_snapshot(checkpoint)
        return res

    def _write_snapshot(self, checkpoint: Checkpoint) -> None:
        channel_values = checkpoint.get("channel_values", {})
        # Find company state channel
        state_candidate = None
        if "company_state" in channel_values:
            state_candidate = channel_values["company_state"]
        elif channel_values and any(k in channel_values for k in ("project_id", "current_phase")):
            state_candidate = channel_values

        if isinstance(state_candidate, dict) and "project_id" in state_candidate:
            save_company_state(state_candidate, self.log_path)  # type: ignore[arg-type]
