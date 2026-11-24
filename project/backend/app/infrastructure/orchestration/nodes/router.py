"""Sub-graph router Command node recording dispatch telemetry before delegation."""

from __future__ import annotations

from typing import Any

from app.domain.graph_state import PHASE_ARCHITECT, GraphState
from app.infrastructure.orchestration.state import ExecutionLogManager
from app.infrastructure.orchestration.subgraphs.base import BaseGraphNode


class RouterNode(BaseGraphNode):
    """Records routing telemetry and inspects completion criteria before sub-graph dispatch."""

    def __init__(self, log_manager: ExecutionLogManager) -> None:
        self._log_manager = log_manager

    @property
    def name(self) -> str:
        return "subgraph_router_node"

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Record dispatch telemetry log entry for the active intent and completed tasks."""
        completed = set(state.get("completed_tasks") or [])
        intent = state.get("intent", "")
        task_type = state.get("task_type", "FULL_PIPELINE")

        logs = self._log_manager.append_to_logs(
            list(state.get("execution_logs") or []),
            agent="subgraph_router",
            phase=state.get("current_phase", PHASE_ARCHITECT),
            message=(
                f"Router: Dispatching workflow for intent='{intent or task_type}' "
                f"(completed={sorted(completed)})."
            ),
            details={
                "intent": intent,
                "task_type": task_type,
                "completed_tasks": list(completed),
            },
        )

        return {
            **state,
            "current_node": self.name,
            "execution_logs": logs,
        }

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Record routing telemetry asynchronously."""
        return self.execute(state)


__all__ = ["RouterNode"]
