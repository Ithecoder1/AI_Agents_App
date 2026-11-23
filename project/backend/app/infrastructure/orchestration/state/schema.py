"""Centralized orchestration state schema and transition payload models."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.graph_state import (
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
    GraphPhase,
    GraphState,
    GraphStatus,
    IntentType,
    create_initial_graph_state,
)


class CompanyState(GraphState):
    """Centralized state shared across all autonomous startup orchestration nodes."""

    pass


@dataclass(frozen=True, slots=True)
class ExecutionLogEntry:
    """Structured execution audit entry appended during graph node transitions."""

    timestamp: str
    agent: str
    phase: str
    message: str
    details: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize log entry to a JSON-compatible dictionary."""
        entry: dict[str, Any] = {
            "timestamp": self.timestamp,
            "agent": self.agent,
            "phase": self.phase,
            "message": self.message,
        }
        if self.details:
            entry["details"] = dict(self.details)
        return entry


@dataclass(frozen=True, slots=True)
class PlanStep:
    """Single step entry produced by the planner node from an execution profile."""

    step: int
    action: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize plan step to a JSON-compatible dictionary."""
        return {
            "step": self.step,
            "action": self.action,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class TriageSnapshot:
    """Telemetry payload recorded under ``subgraph_results['triage']``."""

    intent: str
    triage_intent: str
    confidence: float
    tier: int
    requires_workspace_manifest: bool
    target_model_tier: str
    token_budget: int
    latency_ms: float
    task_type: str
    is_lightweight: bool
    fast_path: bool
    active_skills: tuple[str, ...]
    subgraphs: tuple[str, ...]
    tools_count: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize triage snapshot to a JSON-compatible dictionary."""
        return {
            "intent": self.intent,
            "triage_intent": self.triage_intent,
            "confidence": round(self.confidence, 4),
            "tier": self.tier,
            "requires_workspace_manifest": self.requires_workspace_manifest,
            "target_model_tier": self.target_model_tier,
            "token_budget": self.token_budget,
            "latency_ms": round(self.latency_ms, 3),
            "task_type": self.task_type,
            "is_lightweight": self.is_lightweight,
            "fast_path": self.fast_path,
            "active_skills": list(self.active_skills),
            "subgraphs": list(self.subgraphs),
            "tools_count": self.tools_count,
        }


def create_initial_state(
    user_requirement: str,
    project_id: str = "vibe-app",
    *,
    initial_phase: str = PHASE_ARCHITECT,
    session_id: str | None = None,
    initial_status: GraphStatus = STATUS_PENDING,
) -> CompanyState:
    """Build a clean CompanyState instance with full GraphState compliance."""
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
            ExecutionLogEntry(
                timestamp=now_iso,
                agent="system",
                phase=initial_phase,
                message=(
                    f"Initialized project {project_id!r} with requirement: {req_clean[:80]!r}"
                ),
            ).to_dict()
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
    entry = ExecutionLogEntry(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        agent=agent,
        phase=phase,
        message=message,
        details=details,
    ).to_dict()
    return [*logs, entry]


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
    "ExecutionLogEntry",
    "GraphPhase",
    "GraphState",
    "GraphStatus",
    "IntentType",
    "PlanStep",
    "TriageSnapshot",
    "append_execution_log",
    "create_initial_graph_state",
    "create_initial_state",
]
