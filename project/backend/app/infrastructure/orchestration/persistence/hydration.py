"""OOP state hydration and persistence service for LangGraph orchestration."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from app.infrastructure.orchestration.state import (
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DEVOPS,
    PHASE_FAILED,
    PHASE_QA,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    ExecutionLogManager,
    GraphState,
    create_initial_graph_state,
)


class GraphStateHydrator:
    """Encapsulates atomic JSON persistence and lossless hydration of ``GraphState``."""

    _OPTIONAL_FIELDS: tuple[str, ...] = (
        "session_id",
        "current_node",
        "messages",
        "active_tools",
        "subgraph_results",
        "status",
        "intent",
        "execution_profile",
        "active_skills",
        "plan",
        "task_type",
    )

    def __init__(
        self,
        default_workspace_dir: Path | None = None,
        *,
        log_manager: ExecutionLogManager | None = None,
    ) -> None:
        self._default_workspace_dir = (
            Path(default_workspace_dir)
            if default_workspace_dir is not None
            else Path("company_workspace")
        )
        self._log_manager = log_manager if log_manager is not None else ExecutionLogManager()
        self._io_lock = threading.RLock()

    @property
    def default_workspace_dir(self) -> Path:
        """Base workspace directory used when no explicit snapshot path is given."""
        return self._default_workspace_dir

    def default_log_path(self) -> Path:
        """Return the default state snapshot JSON path within the workspace."""
        return self._default_workspace_dir / "company_state.json"

    @staticmethod
    def _atomic_replace(tmp_path: Path, target: Path, *, max_attempts: int = 8) -> None:
        for attempt in range(max_attempts):
            try:
                os.replace(tmp_path, target)
                return
            except PermissionError:
                if attempt == max_attempts - 1:
                    raise
                time.sleep(0.01 * (attempt + 1))

    def save(self, state: GraphState, file_path: Path | str | None = None) -> Path:
        """Atomically persist ``state`` to JSON on disk and return the target path."""
        target = Path(file_path) if file_path is not None else self.default_log_path()
        payload = json.dumps(dict(state), indent=2, default=str)

        with self._io_lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(target.parent),
                prefix=f".{target.stem}_",
                suffix=".tmp",
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._atomic_replace(tmp_path, target)
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                raise
        return target

    def load(self, file_path: Path | str | None = None) -> GraphState | None:
        """Load and normalize a persisted ``GraphState`` snapshot, or return ``None``."""
        target = Path(file_path) if file_path is not None else self.default_log_path()
        with self._io_lock:
            if not target.is_file():
                return None

            try:
                raw_obj: object = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None

        if not isinstance(raw_obj, dict):
            return None
        if "project_id" not in raw_obj or "user_requirement" not in raw_obj:
            return None

        session_raw = raw_obj.get("session_id")
        hydrated = create_initial_graph_state(
            str(raw_obj.get("user_requirement", "")),
            project_id=str(raw_obj.get("project_id", "vibe-app")),
            session_id=str(session_raw) if isinstance(session_raw, str) and session_raw else None,
            auto_approve=bool(raw_obj.get("auto_approve", True)),
            max_retries=int(raw_obj.get("max_retries", 3)),
        )

        self._apply_workflow_fields(raw_obj, hydrated)
        self._copy_optional_graph_fields(raw_obj, hydrated)
        return hydrated

    def can_resume(self, state: GraphState | None) -> bool:
        """Return ``True`` if ``state`` represents an in-progress resumable execution."""
        if not state:
            return False
        phase = str(state.get("current_phase", ""))
        status = str(state.get("status", ""))
        if phase in (PHASE_COMPLETED, PHASE_FAILED) or status in (STATUS_COMPLETED, STATUS_FAILED):
            return False
        return bool(
            state.get("user_requirement")
            or state.get("architecture_spec")
            or state.get("code_files")
            or state.get("completed_tasks")
            or state.get("plan")
        )

    def determine_next_incomplete_phase(self, state: GraphState) -> str:
        """Infer the next incomplete execution phase from persisted artifacts and tasks."""
        current_phase = str(state.get("current_phase", ""))
        if current_phase in (PHASE_COMPLETED, PHASE_FAILED):
            return current_phase

        completed = {str(task) for task in state.get("completed_tasks", [])}
        if "architecture" not in completed and not state.get("architecture_spec"):
            return PHASE_ARCHITECT
        if "code" not in completed:
            return PHASE_CODER
        if "devops" not in completed:
            return PHASE_DEVOPS
        if "qa" not in completed:
            return PHASE_QA
        return PHASE_COMPLETED

    def hydrate(self, state: GraphState) -> GraphState:
        """Prepare a loaded ``GraphState`` snapshot for resumed execution."""
        next_phase = self.determine_next_incomplete_phase(state)
        existing_logs = list(state.get("execution_logs", []))
        updated_logs = self._log_manager.append_to_logs(
            existing_logs,
            agent="Persistence",
            phase="RESUME",
            message=(
                f"Resuming execution from phase '{next_phase}' "
                f"(completed={list(state.get('completed_tasks', []))})."
            ),
            details={"resumed_phase": next_phase},
        )
        hydrated: GraphState = {
            **state,
            "current_phase": next_phase,
            "execution_logs": updated_logs,
        }
        if next_phase not in (PHASE_COMPLETED, PHASE_FAILED):
            hydrated["status"] = STATUS_RUNNING
        return hydrated

    @staticmethod
    def _apply_workflow_fields(source: Mapping[str, Any], target: GraphState) -> None:
        if "current_phase" in source and isinstance(source["current_phase"], str):
            target["current_phase"] = source["current_phase"]
        if "architecture_spec" in source:
            arch = source["architecture_spec"]
            target["architecture_spec"] = str(arch) if arch is not None else None
        if isinstance(source.get("code_files"), Mapping):
            target["code_files"] = {str(k): str(v) for k, v in dict(source["code_files"]).items()}
        if "test_results" in source:
            tr = source["test_results"]
            target["test_results"] = str(tr) if tr is not None else None
        if isinstance(source.get("completed_tasks"), list):
            target["completed_tasks"] = [str(item) for item in source["completed_tasks"]]
        if isinstance(source.get("failed_tasks"), list):
            target["failed_tasks"] = [str(item) for item in source["failed_tasks"]]
        if "error_message" in source:
            err = source["error_message"]
            target["error_message"] = str(err) if err is not None else None
        if "retry_count" in source and isinstance(source["retry_count"], int):
            target["retry_count"] = source["retry_count"]
        if isinstance(source.get("execution_logs"), list):
            target["execution_logs"] = [
                dict(entry) if isinstance(entry, Mapping) else {"message": str(entry)}
                for entry in source["execution_logs"]
            ]

    @classmethod
    def _copy_optional_graph_fields(cls, source: Mapping[str, Any], target: GraphState) -> None:
        mutable_target = cast("dict[str, Any]", target)
        for field_name in cls._OPTIONAL_FIELDS:
            if field_name not in source:
                continue
            value = source[field_name]
            if isinstance(value, list):
                mutable_target[field_name] = list(value)
            elif isinstance(value, dict):
                mutable_target[field_name] = dict(value)
            else:
                mutable_target[field_name] = value


__all__ = ["GraphStateHydrator"]
