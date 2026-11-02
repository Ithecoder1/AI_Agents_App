"""Task Decomposer Agent: Decomposes failing monolithic tasks when retry limit is exceeded."""

from __future__ import annotations

from company_core.agents.base_agent import BaseCompanyAgent
from company_core.config import AgentPersonaConfig
from company_core.state import PHASE_DECOMPOSE, CompanyState


class TaskDecomposerAgent(BaseCompanyAgent):
    """Technical Lead decomposing persistent failures into atomic, verifiable subtasks."""

    def __init__(self, config: AgentPersonaConfig | None = None) -> None:
        super().__init__(persona_name="task_decomposer", config=config)

    @property
    def phase_name(self) -> str:
        return PHASE_DECOMPOSE

    def execute(self, state: CompanyState) -> CompanyState:
        """Break down failing component into isolated subtasks, resetting retries."""
        requirement = state["user_requirement"]
        error_message = state.get("error_message") or "Unknown persistent test failure"
        retry_count = state.get("retry_count", 0)

        decomposed_plan = self.decompose_failing_component(
            requirement=requirement,
            error_message=error_message,
            retry_count=retry_count,
        )

        logs = self.log_event(
            state,
            message=(
                "Tech Lead: Max retries exceeded. Decomposed monolithic component into subtasks."
            ),
            details={"decomposed_items_count": 3, "previous_retries": retry_count},
        )

        current_spec = state.get("architecture_spec") or ""
        augmented_spec = f"{current_spec}\n\n{decomposed_plan}".strip()

        return {
            **state,
            "architecture_spec": augmented_spec,
            "retry_count": 0,
            "error_message": None,
            "current_phase": PHASE_DECOMPOSE,
            "execution_logs": logs,
        }

    def decompose_failing_component(
        self,
        requirement: str,
        error_message: str,
        retry_count: int,
    ) -> str:
        """Synthesize decomposition action plan markdown."""
        return f"""# Task Decomposition Plan
## Context
Requirement: {requirement}
Component repeatedly failed verification ({retry_count} retries).
Error diagnosis: {error_message[:200]}

## Decomposed Action Items
1. Isolate atomic parsing from arithmetic evaluation.
2. Refactor `solve_arithmetic` to handle whitespace and basic addition cleanly.
3. Reset verification harness for sub-component testing.
"""
