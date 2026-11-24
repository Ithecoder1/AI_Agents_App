"""DEPRECATED: Compatibility shim for centralized orchestration state schema.

Prefer ``app.domain.graph_state`` and ``app.infrastructure.orchestration.state``.
"""

from __future__ import annotations

__deprecated__ = True

from typing import Any

from app.infrastructure.orchestration.state.schema import (
    ALL_GRAPH_STATUSES,
    ALL_INTENT_TYPES,
    ALL_PHASES,
    INTENT_ARCHITECT_PIPELINE,
    INTENT_ARCHITECTURE_DESIGN,
    INTENT_CODE_TEST_FIX,
    INTENT_COMPLEX_PIPELINE,
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DECOMPOSE,
    PHASE_DEVOPS,
    PHASE_FAILED,
    PHASE_QA,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    CompanyState,
    CompanyStateFactory,
    ExecutionLogManager,
    GraphPhase,
    GraphState,
    GraphStatus,
    IntentType,
    create_initial_graph_state,
)

_LOG_MANAGER = ExecutionLogManager()
_STATE_FACTORY = CompanyStateFactory(_LOG_MANAGER)


def create_initial_state(
    user_requirement: str,
    project_id: str = "vibe-app",
    *,
    initial_phase: str = PHASE_ARCHITECT,
    session_id: str | None = None,
    initial_status: GraphStatus = STATUS_PENDING,
) -> CompanyState:
    """Thin backward-compatible delegate to ``CompanyStateFactory.create_initial_state``."""
    return _STATE_FACTORY.create_initial_state(
        user_requirement,
        project_id=project_id,
        initial_phase=initial_phase,
        session_id=session_id,
        initial_status=initial_status,
    )


def append_execution_log(
    logs: list[dict[str, Any]],
    agent: str,
    phase: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Thin backward-compatible delegate to ``ExecutionLogManager.append_to_logs``."""
    return _LOG_MANAGER.append_to_logs(
        logs, agent=agent, phase=phase, message=message, details=details
    )


__all__ = [
    "ALL_GRAPH_STATUSES",
    "ALL_INTENT_TYPES",
    "ALL_PHASES",
    "INTENT_ARCHITECTURE_DESIGN",
    "INTENT_ARCHITECT_PIPELINE",
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
    "CompanyStateFactory",
    "ExecutionLogManager",
    "GraphPhase",
    "GraphState",
    "GraphStatus",
    "IntentType",
    "append_execution_log",
    "create_initial_graph_state",
    "create_initial_state",
]
