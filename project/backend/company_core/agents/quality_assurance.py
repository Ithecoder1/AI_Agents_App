"""Quality Assurance Agent: Executes pytest suite, extracts tracebacks, and evaluates verdicts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from langgraph.graph import END

from company_core.agents.base_agent import BaseCompanyAgent
from company_core.config import AgentPersonaConfig
from company_core.state import (
    PHASE_COMPLETED,
    PHASE_FAILED,
    PHASE_QA,
    CompanyState,
)
from company_core.tools import run_pytest_suite


class QualityAssuranceAgent(BaseCompanyAgent):
    """Lead QA Engineer executing test suites and extracting diagnostic failure tracebacks."""

    def __init__(
        self,
        project_root_resolver: Callable[[str], Path] | None = None,
        config: AgentPersonaConfig | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(persona_name="qa", config=config)
        self.project_root_resolver = project_root_resolver
        self.timeout_seconds = timeout_seconds

    @property
    def phase_name(self) -> str:
        return PHASE_QA

    def resolve_project_dir(self, project_id: str) -> Path:
        """Resolve workspace destination directory on local disk."""
        if self.project_root_resolver:
            return self.project_root_resolver(project_id)
        base = Path.cwd() / "projects"
        base.mkdir(parents=True, exist_ok=True)
        target = base / project_id
        target.mkdir(parents=True, exist_ok=True)
        return target

    def execute(self, state: CompanyState) -> CompanyState:
        """Execute pytest in project directory and record result."""
        project_dir = self.resolve_project_dir(state["project_id"])

        success, _combined_output, clean_traceback = run_pytest_suite(
            project_dir, timeout_s=self.timeout_seconds
        )

        completed = list(state.get("completed_tasks") or [])
        failed = list(state.get("failed_tasks") or [])
        retry_count = state.get("retry_count", 0)

        if success:
            if "qa" not in completed:
                completed.append("qa")
            msg = "QA: All automated tests PASSED successfully."
            error_msg = None
            test_results = "PASSED: All tests succeeded."
            next_phase = PHASE_COMPLETED
        else:
            retry_count += 1
            failed.append(f"qa_attempt_{retry_count}")
            if "code" in completed:
                completed.remove("code")
            msg = (
                f"QA: Tests FAILED on attempt {retry_count}. "
                "Extracted traceback for self-correction."
            )
            error_msg = clean_traceback
            test_results = f"FAILED:\n{clean_traceback}"
            next_phase = PHASE_FAILED

        logs = self.log_event(
            state,
            message=msg,
            details={"success": success, "attempt": retry_count},
        )

        return {
            **state,
            "test_results": test_results,
            "error_message": error_msg,
            "completed_tasks": completed,
            "failed_tasks": failed,
            "retry_count": retry_count,
            "current_phase": next_phase,
            "execution_logs": logs,
        }

    def evaluate_verdict(self, state: CompanyState, max_retries: int = 3) -> str:
        """LangGraph conditional edge evaluator after QA run.

        Routing decisions:
        - If tests pass -> END (COMPLETED)
        - If tests fail & retry_count < max_retries -> 'coder' for self-correction
        - If tests fail & retry_count >= max_retries -> 'task_decomposer'
        """
        test_results = state.get("test_results")
        if state.get("current_phase") == PHASE_COMPLETED or (
            test_results is not None and "PASSED" in test_results
        ):
            return END

        retries = state.get("retry_count", 0)
        if retries >= max_retries:
            return "task_decomposer"

        return "coder"
