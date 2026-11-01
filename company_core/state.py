"""Centralized Graph State Schema for the LangGraph Autonomous Startup Orchestrator."""

from __future__ import annotations

import datetime
from typing import Any, TypedDict

# Canonical Phase Enumerations
PHASE_ARCHITECT = "ARCHITECT"
PHASE_CODER = "CODER"
PHASE_DEVOPS = "DEVOPS"
PHASE_QA = "QA"
PHASE_DECOMPOSE = "DECOMPOSE"
PHASE_COMPLETED = "COMPLETED"
PHASE_FAILED = "FAILED"

ALL_PHASES = (
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_DEVOPS,
    PHASE_QA,
    PHASE_DECOMPOSE,
    PHASE_COMPLETED,
    PHASE_FAILED,
)


class CompanyState(TypedDict):
    """Centralized state shared across all autonomous startup agents."""

    project_id: str
    user_requirement: str
    current_phase: (
        str  # "ARCHITECT" | "CODER" | "QA" | "DEVOPS" | "COMPLETED" | "FAILED"
    )
    architecture_spec: str | None
    code_files: dict[str, str]  # filepath -> file content
    test_results: str | None
    execution_logs: list[dict[str, Any]]
    completed_tasks: list[str]
    failed_tasks: list[str]
    error_message: str | None
    retry_count: int


def create_initial_state(
    user_requirement: str,
    project_id: str = "vibe-app",
    *,
    initial_phase: str = PHASE_ARCHITECT,
) -> CompanyState:
    """Factory helper to build a clean CompanyState instance."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()  # noqa: UP017
    return CompanyState(
        project_id=project_id,
        user_requirement=user_requirement.strip(),
        current_phase=initial_phase,
        architecture_spec=None,
        code_files={},
        test_results=None,
        execution_logs=[
            {
                "timestamp": now_iso,
                "agent": "system",
                "phase": initial_phase,
                "message": f"Initialized project {project_id!r} with requirement: {user_requirement[:80]!r}",
            }
        ],
        completed_tasks=[],
        failed_tasks=[],
        error_message=None,
        retry_count=0,
    )


def append_execution_log(
    logs: list[dict[str, Any]],
    agent: str,
    phase: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return a new copy of execution_logs with an appended event entry."""
    entry: dict[str, Any] = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),  # noqa: UP017
        "agent": agent,
        "phase": phase,
        "message": message,
    }
    if details:
        entry["details"] = details
    return [*logs, entry]
