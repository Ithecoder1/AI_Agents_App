"""Domain services for code synthesis, DevOps setup, QA evaluation, routing, and decomposition."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END

from app.domain.graph_state import (
    PHASE_COMPLETED,
    PHASE_FAILED,
    PHASE_QA,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    GraphState,
)
from app.domain.ports import TestRunResult
from app.infrastructure.orchestration.state import ExecutionLogManager


class CodeSynthesisService:
    """Synthesizes deterministic application source files, unit tests, and repair patches."""

    def synthesize(self, requirement: str, *, simulate_bug: bool = False) -> dict[str, str]:
        """Generate initial Python source modules and pytest unit tests for ``requirement``."""
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

    def apply_self_correction(self, requirement: str, error_context: str) -> dict[str, str]:
        """Synthesize repaired implementation informed by failing QA ``error_context``."""
        _ = error_context
        return self.synthesize(requirement, simulate_bug=False)


class DevOpsEnvironmentService:
    """Produces DevOps dependency manifests and workspace execution guides."""

    def build_environment_files(self, project_id: str) -> dict[str, str]:
        """Build ``requirements.txt`` and ``README.md`` fixtures for ``project_id``."""
        return {
            "requirements.txt": "pytest>=7.0.0\n",
            "README.md": (
                f"# {project_id}\n\n"
                "Automated workspace built by LangGraph Autonomous Startup Orchestrator.\n"
            ),
        }


class TestResultEvaluator:
    """Evaluates ``TestRunResult`` outcomes into deterministic ``GraphState`` transitions."""

    def evaluate(
        self,
        state: GraphState,
        result: TestRunResult,
        log_manager: ExecutionLogManager,
    ) -> dict[str, Any]:
        """Translate ``result`` into updated tasks, retry counter, status, and QA logs."""
        completed = list(state.get("completed_tasks") or [])
        failed = list(state.get("failed_tasks") or [])
        retry_count = int(state.get("retry_count", 0))

        if result.success:
            if "qa" not in completed:
                completed.append("qa")
            msg = "QA: All automated tests PASSED successfully."
            error_msg: str | None = None
            test_results = "PASSED: All tests succeeded."
            next_phase = PHASE_COMPLETED
            next_status = STATUS_COMPLETED
        else:
            retry_count += 1
            failed.append(f"qa_attempt_{retry_count}")
            if "code" in completed:
                completed.remove("code")
            msg = (
                f"QA: Tests FAILED on attempt {retry_count}. "
                "Extracted traceback for self-correction."
            )
            error_msg = result.clean_traceback or result.combined_output
            test_results = f"FAILED:\n{error_msg}"
            next_phase = PHASE_FAILED
            next_status = state.get("status", STATUS_RUNNING)

        logs = log_manager.append_to_logs(
            list(state.get("execution_logs") or []),
            agent="qa",
            phase=PHASE_QA,
            message=msg,
            details={"success": result.success, "attempt": retry_count},
        )

        sub_results = dict(state.get("subgraph_results") or {})
        sub_results["qa"] = {
            "passed": result.success,
            "test_summary": test_results.splitlines()[0] if test_results else "No output",
            "retry_count": retry_count,
        }

        return {
            **state,
            "test_results": test_results,
            "error_message": error_msg,
            "completed_tasks": completed,
            "failed_tasks": failed,
            "retry_count": retry_count,
            "current_phase": next_phase,
            "status": next_status,
            "current_node": "evaluator_node",
            "subgraph_results": sub_results,
            "execution_logs": logs,
        }


class CoderQARoutePolicy:
    """Conditional edge policy determining self-correction loop vs sub-graph exit."""

    def __init__(self, default_max_retries: int = 3) -> None:
        self._default_max_retries = default_max_retries

    @property
    def default_max_retries(self) -> int:
        """Default maximum QA retries when not overridden on ``GraphState``."""
        return self._default_max_retries

    def decide(self, state: GraphState) -> str:
        """Return ``'coder_node'`` when tests fail under retry budget, else ``END``."""
        test_results = state.get("test_results") or ""
        if "PASSED" in test_results:
            return str(END)

        retries = int(state.get("retry_count", 0))
        limit = int(state.get("max_retries", self._default_max_retries))
        if retries < limit:
            return "coder_node"
        return str(END)

    def __call__(self, state: GraphState) -> str:
        """Invoke conditional edge routing decision."""
        return self.decide(state)


class TaskDecompositionStrategy:
    """Breaks persistent failing components into smaller verifiable subtasks."""

    def decompose(self, requirement: str, error_message: str, retry_count: int) -> str:
        """Synthesize a decomposition markdown plan after QA retries are exhausted."""
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


__all__ = [
    "CodeSynthesisService",
    "CoderQARoutePolicy",
    "DevOpsEnvironmentService",
    "TaskDecompositionStrategy",
    "TestResultEvaluator",
]
