"""Planner Command node for structured plan synthesis and human approval gating."""

from __future__ import annotations

from typing import Any

from app.domain.graph_state import (
    PHASE_ARCHITECT,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    GraphState,
)
from app.domain.ports import IProfileClassifier
from app.infrastructure.orchestration.state import ExecutionLogManager
from app.infrastructure.orchestration.subgraphs.base import BaseGraphNode


class PlannerNode(BaseGraphNode):
    """Synthesizes an execution plan from the active profile and applies approval gating."""

    def __init__(
        self,
        profile_classifier: IProfileClassifier,
        log_manager: ExecutionLogManager,
    ) -> None:
        self._profile_classifier = profile_classifier
        self._log_manager = log_manager

    @property
    def name(self) -> str:
        return "planner_node"

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Build structured execution plan and transition to ``WAITING_APPROVAL`` if required."""
        intent_or_type = state.get("intent") or state.get("task_type") or "FULL_PIPELINE"
        profile = self._profile_classifier.get_profile(intent_or_type)
        auto_approve = state.get("auto_approve", True)

        state_task_type = state.get("task_type")
        if state_task_type and state_task_type != "DEFAULT":
            override_profile = self._profile_classifier.get_profile(state_task_type)
            if override_profile.task_type == state_task_type:
                profile = override_profile

        plan = [dict(step) for step in profile.plan_template]
        if not plan:
            plan = [
                {
                    "step": 1,
                    "action": "direct_execution",
                    "description": "Execute user request",
                }
            ]

        logs = self._log_manager.append_to_logs(
            list(state.get("execution_logs") or []),
            agent="ceo_planner",
            phase=PHASE_ARCHITECT,
            message=(f"Planner: Synthesized {len(plan)}-step plan for profile '{profile.intent}'."),
            details={
                "intent": profile.intent,
                "plan": plan,
                "auto_approved": auto_approve,
            },
        )

        needs_gate = (
            not auto_approve and profile.requires_approval and profile.task_type != "DIRECT_QA"
        )
        next_status = STATUS_WAITING_APPROVAL if needs_gate else STATUS_RUNNING

        return {
            **state,
            "plan": plan,
            "status": next_status,
            "current_node": self.name,
            "execution_logs": logs,
        }

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Execute plan synthesis asynchronously."""
        return self.execute(state)


__all__ = ["PlannerNode"]
