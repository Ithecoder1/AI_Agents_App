"""Software Engineer Agent: Implements code files and applies self-correction patches."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from company_core.agents.base_agent import BaseCompanyAgent
from company_core.config import AgentPersonaConfig
from company_core.state import PHASE_CODER, PHASE_DEVOPS, CompanyState
from company_core.tools import summarize_code_for_context, write_project_files


class SoftwareEngineerAgent(BaseCompanyAgent):
    """Lead Software Engineer implementing source code, fixtures, and self-correction patches."""

    def __init__(
        self,
        project_root_resolver: Callable[[str], Path] | None = None,
        config: AgentPersonaConfig | None = None,
        simulate_initial_bug: bool = False,
    ) -> None:
        super().__init__(persona_name="coder", config=config)
        self.project_root_resolver = project_root_resolver
        self.simulate_initial_bug = simulate_initial_bug

    @property
    def phase_name(self) -> str:
        return PHASE_CODER

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
        """Write or patch code files according to architecture spec and test results."""
        project_dir = self.resolve_project_dir(state["project_id"])
        retry_count = state.get("retry_count", 0)
        has_error = bool(state.get("error_message"))

        # Context window hygiene: summarize existing files
        existing_files = state.get("code_files") or {}
        code_summary = summarize_code_for_context(existing_files)

        # Self-correction check: if QA failed and we are retrying
        if has_error and retry_count > 0:
            error_msg = state.get("error_message") or ""
            code_files = self.apply_self_correction(
                requirement=state["user_requirement"],
                error_context=error_msg,
            )
            log_msg = (
                "Coder: Applying self-correction patch based on QA traceback "
                f"(Retry #{retry_count})."
            )
        else:
            code_files = self.synthesize_code(
                requirement=state["user_requirement"],
                simulate_bug=self.simulate_initial_bug,
            )
            log_msg = "Coder: Implemented core source files and test fixtures."

        # Merge new files with existing files
        merged_files = {**existing_files, **code_files}

        # Persist code files directly to project workspace disk
        write_project_files(project_dir, merged_files)

        completed = list(state.get("completed_tasks") or [])
        if "code" not in completed:
            completed.append("code")

        logs = self.log_event(
            state,
            message=log_msg,
            details={
                "written_files": list(code_files.keys()),
                "summary_tokens_est": len(code_summary) // 4,
            },
        )

        return {
            **state,
            "code_files": merged_files,
            "completed_tasks": completed,
            "current_phase": PHASE_DEVOPS,
            "execution_logs": logs,
        }

    def synthesize_code(
        self,
        requirement: str,
        simulate_bug: bool = False,
    ) -> dict[str, str]:
        """Synthesize source code files and unit tests for the given requirement."""
        buggy_result = "return a + b + 999  # Simulated bug"
        clean_result = "return a + b"
        eval_body = buggy_result if simulate_bug else clean_result

        engine_py = f'''"""Arithmetic Evaluator Engine.

Evaluates mathematical expressions deterministically.
Requirement: {requirement}
"""

from __future__ import annotations


def solve_arithmetic(expression: str) -> int:
    """Evaluate simple arithmetic expression like '1+1' or '2 + 3'.

    Raises ValueError on invalid syntax or unsupported operations.
    """
    clean = expression.replace(" ", "").strip()
    if not clean:
        raise ValueError("Expression cannot be empty")

    if "+" in clean:
        parts = clean.split("+")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            a, b = int(parts[0]), int(parts[1])
            {eval_body}

    raise ValueError(f"Unsupported arithmetic expression: {{expression!r}}")
'''

        test_engine_py = '''"""Unit tests for Arithmetic Evaluator Engine."""

from __future__ import annotations

import pytest
from app.engine import solve_arithmetic


def test_solve_simple_addition():
    assert solve_arithmetic("1+1") == 2
    assert solve_arithmetic("2 + 3") == 5
    assert solve_arithmetic("0 + 0") == 0


def test_solve_invalid_expression():
    with pytest.raises(ValueError):
        solve_arithmetic("")

    with pytest.raises(ValueError):
        solve_arithmetic("10 - 5")
'''

        return {
            "app/__init__.py": "",
            "app/engine.py": engine_py,
            "tests/__init__.py": "",
            "tests/test_engine.py": test_engine_py,
        }

    def apply_self_correction(
        self,
        requirement: str,
        error_context: str,
    ) -> dict[str, str]:
        """Produce bug-free implementation using diagnostic error context."""
        return self.synthesize_code(requirement, simulate_bug=False)
