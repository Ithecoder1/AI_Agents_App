"""Centralized Graph State Schema for the LangGraph Autonomous Startup Orchestrator."""

from __future__ import annotations

import datetime
from typing import Any

from app.domain.graph_state import (
    ALL_GRAPH_STATUSES,
    ALL_INTENT_TYPES,
    INTENT_ARCHITECTURE_DESIGN,
    INTENT_CODE_TEST_FIX,
    INTENT_COMPLEX_PIPELINE,
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    GraphState,
    GraphStatus,
    IntentType,
    create_initial_graph_state,
)

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


class CompanyState(GraphState):
    """Centralized state shared across all autonomous startup agents."""

    pass


def create_initial_state(
    user_requirement: str,
    project_id: str = "vibe-app",
    *,
    initial_phase: str = PHASE_ARCHITECT,
    session_id: str | None = None,
    initial_status: GraphStatus = STATUS_PENDING,
) -> CompanyState:
    """Factory helper to build a clean CompanyState instance with GraphState compliance."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sid = session_id or f"sess-{project_id}"
    req_clean = user_requirement.strip()

    return CompanyState(
        session_id=sid,
        project_id=project_id,
        user_requirement=req_clean,
        current_node="triage_node",
        messages=[{"role": "user", "content": req_clean}],
        active_tools=[],
        subgraph_results={},
        execution_logs=[
            {
                "timestamp": now_iso,
                "agent": "system",
                "phase": initial_phase,
                "message": (
                    f"Initialized project {project_id!r} with requirement: {req_clean[:80]!r}"
                ),
            }
        ],
        status=initial_status,
        intent=INTENT_COMPLEX_PIPELINE,
        execution_profile={},
        active_skills=[],
        current_phase=initial_phase,
        architecture_spec=None,
        code_files={},
        test_results=None,
        completed_tasks=[],
        failed_tasks=[],
        error_message=None,
        retry_count=0,
        max_retries=3,
        auto_approve=True,
        plan=[],
        task_type="DEFAULT",
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
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "agent": agent,
        "phase": phase,
        "message": message,
    }
    if details:
        entry["details"] = details
    return [*logs, entry]


__all__ = [
    "ALL_GRAPH_STATUSES",
    "ALL_INTENT_TYPES",
    "ALL_PHASES",
    "INTENT_ARCHITECTURE_DESIGN",
    "INTENT_CODE_TEST_FIX",
    "INTENT_COMPLEX_PIPELINE",
    "INTENT_DIRECT_QA",
    "INTENT_FOCUSED_TOOL_TASK",
    "PHASE_ARCHITECT",
    "PHASE_CODER",
    "PHASE_COMPLETED",
    "PHASE_DECOMPOSE",
    "PHASE_DEVOPS",
    "PHASE_FAILED",
    "PHASE_QA",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_WAITING_APPROVAL",
    "CompanyState",
    "GraphState",
    "GraphStatus",
    "IntentType",
    "append_execution_log",
    "create_initial_graph_state",
    "create_initial_state",
]
