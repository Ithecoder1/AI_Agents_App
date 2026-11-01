"""CEO Orchestrator Agent: Analyzes requirements and coordinates operational phases."""

from __future__ import annotations

from langgraph.graph import END

from company_core.agents.base_agent import BaseCompanyAgent
from company_core.config import AgentPersonaConfig
from company_core.state import (
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DEVOPS,
    PHASE_QA,
    CompanyState,
)


class CeoOrchestratorAgent(BaseCompanyAgent):
    """Chief Executive Officer orchestrator deciding operational roadmap and next phase."""

    def __init__(self, config: AgentPersonaConfig | None = None) -> None:
        super().__init__(persona_name="ceo", config=config)

    @property
    def phase_name(self) -> str:
        return PHASE_ARCHITECT

    def execute(self, state: CompanyState) -> CompanyState:
        """Evaluate requirements and state to advance the pipeline phase."""
        completed = set(state.get("completed_tasks") or [])
        current = state.get("current_phase")

        if "architecture" not in completed or not state.get("architecture_spec"):
            next_phase = PHASE_ARCHITECT
            log_msg = "CEO: Analyzing user requirement; dispatching task to Software Architect."
        elif "code" not in completed or not state.get("code_files"):
            next_phase = PHASE_CODER
            log_msg = (
                "CEO: Architecture approved; routing execution to Software Engineer."
            )
        elif "devops" not in completed:
            next_phase = PHASE_DEVOPS
            log_msg = "CEO: Code implemented; routing to DevOps Engineer for test harness setup."
        elif "qa" not in completed:
            next_phase = PHASE_QA
            log_msg = "CEO: Environment ready; routing to QA Engineer for verification."
        else:
            next_phase = PHASE_COMPLETED
            log_msg = "CEO: All milestone tasks completed and verified."

        logs = self.log_event(
            state,
            message=log_msg,
            details={
                "previous_phase": current,
                "next_phase": next_phase,
                "completed_tasks": list(completed),
            },
        )

        return {
            **state,
            "current_phase": next_phase,
            "execution_logs": logs,
        }

    def route_next_phase(self, state: CompanyState) -> str:
        """LangGraph conditional routing function mapping state phase to target node."""
        phase = state.get("current_phase")
        if phase == PHASE_ARCHITECT:
            return "architect"
        if phase == PHASE_CODER:
            return "coder"
        if phase == PHASE_DEVOPS:
            return "devops"
        if phase == PHASE_QA:
            return "qa"
        return END
