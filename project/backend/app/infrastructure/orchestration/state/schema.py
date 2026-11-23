"""Object-oriented orchestration state schemas, payload models, and transition managers."""

from __future__ import annotations

import datetime
import math
from collections.abc import Callable, Mapping, Sequence
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
    """Validated execution audit entry appended during graph node transitions."""

    timestamp: str
    agent: str
    phase: str
    message: str
    details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.agent.strip():
            raise ValueError("ExecutionLogEntry 'agent' must not be empty")
        if not self.phase.strip():
            raise ValueError("ExecutionLogEntry 'phase' must not be empty")

    @classmethod
    def create(
        cls,
        *,
        agent: str,
        phase: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> ExecutionLogEntry:
        """Create a validated log entry stamped with current UTC ISO time if omitted."""
        ts = timestamp or datetime.datetime.now(datetime.timezone.utc).isoformat()
        return cls(
            timestamp=ts,
            agent=agent,
            phase=phase,
            message=message,
            details=dict(details) if details is not None else None,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionLogEntry:
        """Hydrate and validate an ``ExecutionLogEntry`` from a mapping."""
        raw_details = data.get("details")
        details = dict(raw_details) if isinstance(raw_details, Mapping) else None
        return cls(
            timestamp=str(data.get("timestamp") or ""),
            agent=str(data.get("agent") or ""),
            phase=str(data.get("phase") or ""),
            message=str(data.get("message") or ""),
            details=details,
        )

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
    """Validated plan step entry produced by the planner node from an execution profile."""

    step: int
    action: str
    description: str

    def __post_init__(self) -> None:
        if self.step < 1:
            raise ValueError(f"PlanStep 'step' must be >= 1, got {self.step!r}")
        if not self.action.strip():
            raise ValueError("PlanStep 'action' must not be empty")
        if not self.description.strip():
            raise ValueError("PlanStep 'description' must not be empty")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, default_step: int = 1) -> PlanStep:
        """Construct a validated ``PlanStep`` from a raw dictionary."""
        raw_step = data.get("step", default_step)
        action = str(data.get("action") or "direct_execution")
        description = str(data.get("description") or action)
        return cls(step=int(raw_step), action=action, description=description)

    def to_dict(self) -> dict[str, Any]:
        """Serialize plan step to a JSON-compatible dictionary."""
        return {
            "step": self.step,
            "action": self.action,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class TriageSnapshot:
    """Validated telemetry payload recorded under ``subgraph_results['triage']``."""

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

    def __post_init__(self) -> None:
        if math.isnan(self.confidence) or not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"TriageSnapshot 'confidence' must be in [0.0, 1.0], got {self.confidence!r}"
            )
        if self.tier not in (1, 2, 3):
            raise ValueError(f"TriageSnapshot 'tier' must be 1, 2, or 3, got {self.tier!r}")
        if self.token_budget <= 0:
            raise ValueError(
                f"TriageSnapshot 'token_budget' must be positive, got {self.token_budget!r}"
            )
        if self.tools_count < 0:
            raise ValueError(f"TriageSnapshot 'tools_count' must be >= 0, got {self.tools_count!r}")

    @classmethod
    def from_classification(
        cls,
        *,
        profile: Any,
        decision: Any,
        active_skills: Sequence[str],
        tools_count: int,
    ) -> TriageSnapshot:
        """Build a validated ``TriageSnapshot`` from a profile and triage decision."""
        return cls(
            intent=str(profile.intent),
            triage_intent=str(decision.intent),
            confidence=float(decision.confidence),
            tier=int(decision.tier),
            requires_workspace_manifest=bool(decision.requires_workspace_manifest),
            target_model_tier=str(decision.target_model_tier),
            token_budget=int(decision.token_budget),
            latency_ms=float(decision.latency_ms),
            task_type=str(profile.task_type),
            is_lightweight=bool(decision.tier == 1 and decision.intent == "DIRECT_QA"),
            fast_path=bool(profile.fast_path),
            active_skills=tuple(str(s) for s in active_skills),
            subgraphs=tuple(str(sg) for sg in profile.subgraphs),
            tools_count=int(tools_count),
        )

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


class ExecutionLogManager:
    """Encapsulates execution log entry creation and immutable transition updates."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))

    def create_entry(
        self,
        *,
        agent: str,
        phase: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> ExecutionLogEntry:
        """Construct a validated ``ExecutionLogEntry`` stamped with this manager's clock."""
        return ExecutionLogEntry.create(
            agent=agent,
            phase=phase,
            message=message,
            details=details,
            timestamp=self._clock().isoformat(),
        )

    def append(
        self,
        state: GraphState,
        node_name: str,
        status: str,
        details: Mapping[str, Any] | None = None,
        *,
        message: str | None = None,
    ) -> list[dict[str, Any]]:
        """Append a node transition log entry for *state* without mutating the input list."""
        existing = list(state.get("execution_logs") or [])
        phase = str(state.get("current_phase") or status)
        merged_details: dict[str, Any] = {"status": status}
        if details:
            merged_details.update(details)
        entry = self.create_entry(
            agent=node_name,
            phase=phase,
            message=message or f"{node_name} transitioned to {status}",
            details=merged_details,
        )
        return [*existing, entry.to_dict()]

    def append_to_logs(
        self,
        logs: Sequence[dict[str, Any]],
        *,
        agent: str,
        phase: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return a copy of *logs* with a newly created entry appended."""
        entry = self.create_entry(agent=agent, phase=phase, message=message, details=details)
        return [*logs, entry.to_dict()]


class CompanyStateFactory:
    """Factory service constructing clean initial ``CompanyState`` instances."""

    def __init__(self, log_manager: ExecutionLogManager | None = None) -> None:
        self._log_manager = log_manager or ExecutionLogManager()

    def create_initial_state(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        *,
        initial_phase: str = PHASE_ARCHITECT,
        session_id: str | None = None,
        initial_status: GraphStatus = STATUS_PENDING,
    ) -> CompanyState:
        """Build a clean ``CompanyState`` instance with full ``GraphState`` compliance."""
        sid = session_id or f"sess-{project_id}"
        req_clean = user_requirement.strip()
        init_entry = self._log_manager.create_entry(
            agent="system",
            phase=initial_phase,
            message=f"Initialized project {project_id!r} with requirement: {req_clean[:80]!r}",
        )

        return CompanyState(
            session_id=sid,
            project_id=project_id,
            user_requirement=req_clean,
            current_node="triage_node",
            messages=[{"role": "user", "content": req_clean}],
            active_tools=[],
            subgraph_results={},
            execution_logs=[init_entry.to_dict()],
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
    "ExecutionLogEntry",
    "ExecutionLogManager",
    "GraphPhase",
    "GraphState",
    "GraphStatus",
    "IntentType",
    "PlanStep",
    "TriageSnapshot",
    "create_initial_graph_state",
]
