"""Lossless JSON state persistence, resumption diagnostics, and hydration helpers."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, cast

from app.domain.graph_state import (
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DEVOPS,
    PHASE_QA,
    GraphState,
)
from app.infrastructure.orchestration.state.schema import (
    CompanyState,
    ExecutionLogManager,
)

_REQUIRED_STATE_KEYS: tuple[str, ...] = (
    "project_id",
    "user_requirement",
    "current_phase",
    "code_files",
    "completed_tasks",
    "retry_count",
)

_OPTIONAL_SCALAR_KEYS: tuple[str, ...] = (
    "session_id",
    "current_node",
    "status",
    "intent",
    "max_retries",
    "auto_approve",
    "task_type",
)


def _default_log_path() -> Path:
    return Path.cwd() / "logs" / "company_state.json"


def _copy_optional_graph_fields(source: dict[str, Any], target: dict[str, Any]) -> None:
    """Copy modern GraphState fields losslessly from *source* into *target* when present."""
    for key in _OPTIONAL_SCALAR_KEYS:
        if key in source and source[key] is not None:
            target[key] = source[key]

    if "messages" in source and isinstance(source["messages"], list):
        target["messages"] = list(source["messages"])
    if "active_tools" in source and isinstance(source["active_tools"], list):
        target["active_tools"] = [
            dict(t) if isinstance(t, dict) else t for t in source["active_tools"]
        ]
    if "subgraph_results" in source and isinstance(source["subgraph_results"], dict):
        target["subgraph_results"] = dict(source["subgraph_results"])
    if "execution_profile" in source and isinstance(source["execution_profile"], dict):
        target["execution_profile"] = dict(source["execution_profile"])
    if "active_skills" in source and isinstance(source["active_skills"], list):
        target["active_skills"] = [str(s) for s in source["active_skills"]]
    if "plan" in source and isinstance(source["plan"], list):
        target["plan"] = [dict(step) if isinstance(step, dict) else step for step in source["plan"]]


def save_graph_state(state: GraphState, filepath: Path | str | None = None) -> Path:
    """Atomically persist GraphState/CompanyState to a JSON file."""
    path = Path(filepath) if filepath else _default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(dict(state), indent=2, ensure_ascii=False, default=str)

    temp_dir = path.parent
    with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
        tf.write(payload)
        temp_name = tf.name

    try:
        os.replace(temp_name, path)
    except PermissionError:
        # On Windows, retry briefly or fallback to direct write on file contention
        replaced = False
        for _ in range(5):
            time.sleep(0.05)
            try:
                os.replace(temp_name, path)
                replaced = True
                break
            except PermissionError:
                pass
        if not replaced:
            path.write_text(payload, encoding="utf-8")
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
    return path


def load_graph_state(filepath: Path | str | None = None) -> CompanyState | None:
    """Load persisted orchestration state losslessly from JSON if valid."""
    path = Path(filepath) if filepath else _default_log_path()
    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if not all(k in data for k in _REQUIRED_STATE_KEYS):
            return None

        raw_code_files = data.get("code_files") or {}
        if not isinstance(raw_code_files, dict):
            return None

        loaded: dict[str, Any] = {
            "project_id": str(data["project_id"]),
            "user_requirement": str(data["user_requirement"]),
            "current_phase": str(data["current_phase"]),
            "architecture_spec": (
                str(data["architecture_spec"])
                if data.get("architecture_spec") is not None
                else None
            ),
            "code_files": {str(k): str(v) for k, v in raw_code_files.items()},
            "test_results": (
                str(data["test_results"]) if data.get("test_results") is not None else None
            ),
            "execution_logs": [
                dict(entry)
                for entry in (data.get("execution_logs") or [])
                if isinstance(entry, dict)
            ],
            "completed_tasks": [str(t) for t in (data.get("completed_tasks") or [])],
            "failed_tasks": [str(t) for t in (data.get("failed_tasks") or [])],
            "error_message": (
                str(data["error_message"]) if data.get("error_message") is not None else None
            ),
            "retry_count": int(data.get("retry_count") or 0),
        }
        _copy_optional_graph_fields(data, loaded)
        return cast(CompanyState, loaded)
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
        return None


def can_resume(state: GraphState | None) -> bool:
    """Return True if *state* represents an interrupted or incomplete execution."""
    if state is None:
        return False
    return state.get("current_phase") != PHASE_COMPLETED


def determine_next_incomplete_phase(state: GraphState) -> str:
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


def hydrate_resumed_state(state: GraphState) -> CompanyState:
    """Hydrate state to resume from the last incomplete phase while preserving GraphState fields."""
    next_phase = determine_next_incomplete_phase(state)
    updated_logs = ExecutionLogManager().append_to_logs(
        list(state.get("execution_logs") or []),
        agent="system",
        phase=next_phase,
        message=(
            f"Resuming execution from incomplete phase: {next_phase}. "
            f"Skipping completed: {state.get('completed_tasks')}"
        ),
    )

    resumed: dict[str, Any] = {
        "project_id": state["project_id"],
        "user_requirement": state["user_requirement"],
        "current_phase": next_phase,
        "architecture_spec": state.get("architecture_spec"),
        "code_files": dict(state.get("code_files") or {}),
        "test_results": state.get("test_results"),
        "execution_logs": updated_logs,
        "completed_tasks": list(state.get("completed_tasks") or []),
        "failed_tasks": list(state.get("failed_tasks") or []),
        "error_message": state.get("error_message"),
        "retry_count": state.get("retry_count", 0),
    }
    _copy_optional_graph_fields(dict(state), resumed)
    return cast(CompanyState, resumed)


save_company_state = save_graph_state
load_company_state = load_graph_state

__all__ = [
    "_default_log_path",
    "can_resume",
    "determine_next_incomplete_phase",
    "hydrate_resumed_state",
    "load_company_state",
    "load_graph_state",
    "save_company_state",
    "save_graph_state",
]
