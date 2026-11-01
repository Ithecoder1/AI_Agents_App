"""DevOps Engineer Agent: Sets up virtual environment, dependencies, and test runner."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from company_core.agents.base_agent import BaseCompanyAgent
from company_core.config import AgentPersonaConfig
from company_core.state import PHASE_DEVOPS, PHASE_QA, CompanyState
from company_core.tools import write_project_files


class DevOpsEngineerAgent(BaseCompanyAgent):
    """DevOps Engineer configuring dependencies, test runner fixtures, and workspace environment."""

    def __init__(
        self,
        project_root_resolver: Callable[[str], Path] | None = None,
        config: AgentPersonaConfig | None = None,
    ) -> None:
        super().__init__(persona_name="devops", config=config)
        self.project_root_resolver = project_root_resolver

    @property
    def phase_name(self) -> str:
        return PHASE_DEVOPS

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
        """Configure requirements and pytest runner in target workspace."""
        project_dir = self.resolve_project_dir(state["project_id"])

        devops_files = {
            "requirements.txt": "pytest>=7.0.0\n",
            "pytest.ini": "[pytest]\npythonpath = .\ntestpaths = tests\n",
            "README.md": f"# {state['project_id']}\n\nAutomated workspace built by LangGraph Autonomous Startup Orchestrator.\n",
        }

        # Persist environment files
        write_project_files(project_dir, devops_files)

        # Merge with existing files in state
        existing = state.get("code_files") or {}
        merged = {**existing, **devops_files}

        completed = list(state.get("completed_tasks") or [])
        if "devops" not in completed:
            completed.append("devops")

        logs = self.log_event(
            state,
            message="DevOps: Configured requirements.txt, pytest.ini, and test execution harness.",
            details={"configured_files": list(devops_files.keys())},
        )

        return {
            **state,
            "code_files": merged,
            "completed_tasks": completed,
            "current_phase": PHASE_QA,
            "execution_logs": logs,
        }
