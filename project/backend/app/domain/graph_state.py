"""Graph state definitions for deterministic LangGraph orchestration.

Pure domain layer (stdlib only).
"""

from __future__ import annotations

import datetime
from typing import Any, Literal, TypedDict

GraphStatus = Literal["PENDING", "RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED"]

STATUS_PENDING: GraphStatus = "PENDING"
STATUS_RUNNING: GraphStatus = "RUNNING"
STATUS_WAITING_APPROVAL: GraphStatus = "WAITING_APPROVAL"
STATUS_COMPLETED: GraphStatus = "COMPLETED"
STATUS_FAILED: GraphStatus = "FAILED"

ALL_GRAPH_STATUSES = (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    STATUS_COMPLETED,
    STATUS_FAILED,
)


class GraphState(TypedDict, total=False):
    """Global State Schema for Master Graph and Sub-Graphs."""

    session_id: str
    project_id: str
    user_requirement: str
    current_node: str
    messages: list[Any]
    active_tools: list[dict[str, Any]]
    subgraph_results: dict[str, Any]
    execution_logs: list[dict[str, Any]]
    status: GraphStatus

    # Backward-compatibility and workflow execution fields
    current_phase: str
    architecture_spec: str | None
    code_files: dict[str, str]
    test_results: str | None
    completed_tasks: list[str]
    failed_tasks: list[str]
    error_message: str | None
    retry_count: int
    max_retries: int
    auto_approve: bool
    plan: list[dict[str, Any]]
    task_type: str


def create_initial_graph_state(
    user_requirement: str,
    project_id: str = "vibe-app",
    session_id: str | None = None,
    *,
    initial_status: GraphStatus = STATUS_PENDING,
    initial_node: str = "triage_node",
    auto_approve: bool = True,
    max_retries: int = 3,
) -> GraphState:
    """Factory helper to build a clean GraphState instance."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sid = session_id or f"sess-{project_id}"
    return GraphState(
        session_id=sid,
        project_id=project_id,
        user_requirement=user_requirement.strip(),
        current_node=initial_node,
        messages=[{"role": "user", "content": user_requirement.strip()}],
        active_tools=[],
        subgraph_results={},
        execution_logs=[
            {
                "timestamp": now_iso,
                "agent": "system",
                "phase": "INITIALIZATION",
                "message": (
                    f"Initialized graph session {sid!r} for project {project_id!r} "
                    f"with requirement: {user_requirement[:80]!r}"
                ),
            }
        ],
        status=initial_status,
        current_phase="ARCHITECT",
        architecture_spec=None,
        code_files={},
        test_results=None,
        completed_tasks=[],
        failed_tasks=[],
        error_message=None,
        retry_count=0,
        max_retries=max_retries,
        auto_approve=auto_approve,
        plan=[],
        task_type="DEFAULT",
    )
