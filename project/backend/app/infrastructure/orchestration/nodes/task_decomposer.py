"""Task Decomposer Command node breaking down persistently failing monolithic components."""

from __future__ import annotations

from typing import Any

from app.domain.graph_state import PHASE_CODER, PHASE_DECOMPOSE, GraphState
from app.infrastructure.orchestration.state import ExecutionLogManager
from app.infrastructure.orchestration.subgraphs.base import BaseGraphNode
from app.infrastructure.orchestration.subgraphs.coder_qa import TaskDecompositionStrategy


class TaskDecomposerNode(BaseGraphNode):
    """Decomposes persistently failing tasks after QA retry exhaustion and resets retry budget."""

    def __init__(
        self,
        decomposition_strategy: TaskDecompositionStrategy,
        log_manager: ExecutionLogManager,
    ) -> None:
        self._decomposition_strategy = decomposition_strategy
        self._log_manager = log_manager

    @property
    def name(self) -> str:
        return "task_decomposer"

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Augment ``architecture_spec`` with decomposed subtasks and reset retries."""
        requirement = state.get("user_requirement", "")
        error_message = state.get("error_message") or "Unknown persistent test failure"
        retry_count = int(state.get("retry_count", 0))

        decomposed_plan = self._decomposition_strategy.decompose(
            requirement=requirement,
            error_message=error_message,
            retry_count=retry_count,
        )
        current_spec = state.get("architecture_spec") or ""
        augmented_spec = f"{current_spec}\n\n{decomposed_plan}".strip()

        logs = self._log_manager.append_to_logs(
            list(state.get("execution_logs") or []),
            agent="task_decomposer",
            phase=PHASE_DECOMPOSE,
            message="Tech Lead: Decomposed monolithic task after repeated test failures.",
            details={"decomposed_items_count": 3, "previous_retries": retry_count},
        )

        return {
            **state,
            "architecture_spec": augmented_spec,
            "retry_count": 0,
            "error_message": None,
            "current_node": "task_decomposer_node",
            "current_phase": PHASE_CODER,
            "execution_logs": logs,
        }

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Execute failure decomposition asynchronously."""
        return self.execute(state)


__all__ = ["TaskDecomposerNode"]
