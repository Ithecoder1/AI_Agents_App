"""LangGraph Workflow Graph & Orchestration Nodes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from company_core.config import get_config
from company_core.persistence import JsonStateCheckpointer
from company_core.state import (
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DECOMPOSE,
    PHASE_DEVOPS,
    PHASE_FAILED,
    PHASE_QA,
    CompanyState,
    append_execution_log,
)
from company_core.tools import (
    run_pytest_suite,
    summarize_code_for_context,
    write_project_files,
)


def _resolve_project_dir(project_id: str, base_dir: Path | str | None = None) -> Path:
    base = Path(base_dir) if base_dir else Path.cwd() / "projects"
    return base / project_id


# --------------------------------------------------------------------------- #
# Synthetic / Default Code Generators (for deterministic runs & testing)
# --------------------------------------------------------------------------- #
def _synthesize_initial_architecture(requirement: str) -> str:
    return f"""# Architecture Specification
## Target Requirement
{requirement}

## Modular Architecture
- Core Logic: `src/core_engine.py` (Domain entity & calculation functions)
- Test Suite: `tests/test_core_engine.py` (Unit tests verifying functional behavior)

## Guidelines
- Pure Python standard library logic for core algorithms.
- Clear error handling for invalid inputs.
- Pytest test cases covering nominal and edge cases.
"""


def _synthesize_initial_code(
    requirement: str, simulate_initial_bug: bool = False
) -> dict[str, str]:
    """Generate initial code and tests.
    Can inject an intentional bug on attempt 0 to demonstrate self-correction.
    """
    is_math = any(k in requirement.lower() for k in ("1+1", "math", "calculator", "add"))

    if is_math:
        if simulate_initial_bug:
            # Bug: returns wrong value on purpose to trigger QA failure
            engine_code = (
                '"""Deterministic Math Engine."""\n\n'
                "def solve_arithmetic(expr: str) -> int:\n"
                '    """Evaluate a simple arithmetic expression."""\n'
                '    clean = expr.strip().replace(" ", "")\n'
                '    if "+" in clean:\n'
                '        parts = clean.split("+")\n'
                "        # Intentional bug: off by one\n"
                "        return int(parts[0]) + int(parts[1]) + 999\n"
                '    raise ValueError(f"Unsupported expression: {expr}")\n'
            )
        else:
            engine_code = (
                '"""Deterministic Math Engine."""\n\n'
                "def solve_arithmetic(expr: str) -> int:\n"
                '    """Evaluate a simple arithmetic expression."""\n'
                '    clean = expr.strip().replace(" ", "")\n'
                '    if "+" in clean:\n'
                '        parts = clean.split("+")\n'
                "        return int(parts[0]) + int(parts[1])\n"
                '    raise ValueError(f"Unsupported expression: {expr}")\n'
            )

        test_code = (
            "from src.core_engine import solve_arithmetic\n\n"
            "def test_solve_one_plus_one():\n"
            '    assert solve_arithmetic("1+1") == 2\n\n'
            "def test_solve_with_spaces():\n"
            '    assert solve_arithmetic(" 1 + 1 ") == 2\n'
        )
    else:
        engine_code = (
            '"""Core Business Engine."""\n\n'
            "def process_task(data: str) -> str:\n"
            '    return f"processed: {data.strip()}"\n'
        )
        test_code = (
            "from src.core_engine import process_task\n\n"
            "def test_process_task():\n"
            '    assert process_task("hello") == "processed: hello"\n'
        )

    return {
        "src/core_engine.py": engine_code,
        "tests/test_core_engine.py": test_code,
    }


# --------------------------------------------------------------------------- #
# Graph Nodes
# --------------------------------------------------------------------------- #
def ceo_router_node(state: CompanyState) -> CompanyState:
    """Analyze the user requirement and current state, decides the next operational phase."""
    completed = set(state.get("completed_tasks") or [])

    if "architecture" not in completed or not state.get("architecture_spec"):
        next_phase = PHASE_ARCHITECT
        msg = "CEO: Initializing project architecture phase."
    elif "code" not in completed:
        next_phase = PHASE_CODER
        msg = "CEO: Architecture approved; routing to Coder."
    elif "devops" not in completed:
        next_phase = PHASE_DEVOPS
        msg = "CEO: Code implemented; routing to DevOps for environment setup."
    elif "qa" not in completed:
        next_phase = PHASE_QA
        msg = "CEO: Environment configured; routing to QA for test verification."
    else:
        next_phase = PHASE_COMPLETED
        msg = "CEO: All milestone tasks verified successfully. Mission complete."

    updated_logs = append_execution_log(
        state.get("execution_logs") or [],
        agent="ceo",
        phase=next_phase,
        message=msg,
        details={"completed_tasks": list(completed)},
    )

    return {
        **state,
        "current_phase": next_phase,
        "execution_logs": updated_logs,
    }


def architect_node(state: CompanyState) -> CompanyState:
    """Generates or updates the system architecture blueprint and directory tree."""
    req = state["user_requirement"]
    spec = _synthesize_initial_architecture(req)

    completed = list(state.get("completed_tasks") or [])
    if "architecture" not in completed:
        completed.append("architecture")

    updated_logs = append_execution_log(
        state.get("execution_logs") or [],
        agent="architect",
        phase=PHASE_ARCHITECT,
        message="Architect: Generated architecture specification and module layout.",
    )

    return {
        **state,
        "architecture_spec": spec,
        "completed_tasks": completed,
        "current_phase": PHASE_CODER,
        "execution_logs": updated_logs,
    }


def make_coder_node(
    project_root_getter: Callable[[str], Path] | None = None,
    simulate_initial_bug: bool = False,
) -> Callable[[CompanyState], CompanyState]:
    """Create a coder_node with injected project directory resolver."""

    def coder_node(state: CompanyState) -> CompanyState:
        project_dir = (
            project_root_getter(state["project_id"])
            if project_root_getter
            else _resolve_project_dir(state["project_id"])
        )

        retry_count = state.get("retry_count", 0)
        has_error = bool(state.get("error_message"))

        # Context Window Hygiene: summarize existing files rather than dumping raw code
        existing_files = state.get("code_files") or {}
        code_summary = summarize_code_for_context(existing_files)

        # Self-correction check: if QA failed and we are retrying, generate the fixed code
        if has_error and retry_count > 0:
            # Self-correct by producing bug-free implementation
            code_files = _synthesize_initial_code(
                state["user_requirement"], simulate_initial_bug=False
            )
            msg = (
                "Coder: Applying self-correction patch based on QA traceback "
                f"(Retry #{retry_count})."
            )
        else:
            # Initial implementation
            code_files = _synthesize_initial_code(
                state["user_requirement"],
                simulate_initial_bug=simulate_initial_bug,
            )
            msg = "Coder: Implemented core source files and test fixtures."

        # Merge with existing files
        merged_files = {**existing_files, **code_files}

        # Write code files directly to project workspace disk
        write_project_files(project_dir, merged_files)

        completed = list(state.get("completed_tasks") or [])
        if "code" not in completed:
            completed.append("code")

        updated_logs = append_execution_log(
            state.get("execution_logs") or [],
            agent="coder",
            phase=PHASE_CODER,
            message=msg,
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
            "execution_logs": updated_logs,
        }

    return coder_node


def make_devops_node(
    project_root_getter: Callable[[str], Path] | None = None,
) -> Callable[[CompanyState], CompanyState]:
    """Create devops_node configuring requirements and test environment."""

    def devops_node(state: CompanyState) -> CompanyState:
        project_dir = (
            project_root_getter(state["project_id"])
            if project_root_getter
            else _resolve_project_dir(state["project_id"])
        )

        devops_files = {
            "requirements.txt": "pytest>=8.0\npytest-asyncio>=0.23\n",
            "pytest.ini": "[pytest]\ntestpaths = tests\npython_files = test_*.py\n",
        }

        merged_files = {**(state.get("code_files") or {}), **devops_files}
        write_project_files(project_dir, devops_files)

        completed = list(state.get("completed_tasks") or [])
        if "devops" not in completed:
            completed.append("devops")

        updated_logs = append_execution_log(
            state.get("execution_logs") or [],
            agent="devops",
            phase=PHASE_DEVOPS,
            message="DevOps: Generated requirements.txt and pytest harness.",
        )

        return {
            **state,
            "code_files": merged_files,
            "completed_tasks": completed,
            "current_phase": PHASE_QA,
            "execution_logs": updated_logs,
        }

    return devops_node


def make_qa_node(
    project_root_getter: Callable[[str], Path] | None = None,
) -> Callable[[CompanyState], CompanyState]:
    """Create qa_node executing pytest suite."""

    def qa_node(state: CompanyState) -> CompanyState:
        project_dir = (
            project_root_getter(state["project_id"])
            if project_root_getter
            else _resolve_project_dir(state["project_id"])
        )

        success, _combined_output, clean_traceback = run_pytest_suite(project_dir)

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
            # If code failed, remove code from completed_tasks so it can be re-executed
            if "code" in completed:
                completed.remove("code")
            msg = (
                f"QA: Tests FAILED on attempt {retry_count}. "
                "Extracted traceback for self-correction."
            )
            error_msg = clean_traceback
            test_results = f"FAILED:\n{clean_traceback}"
            next_phase = PHASE_FAILED

        updated_logs = append_execution_log(
            state.get("execution_logs") or [],
            agent="qa",
            phase=PHASE_QA,
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
            "execution_logs": updated_logs,
        }

    return qa_node


def task_decomposer_node(state: CompanyState) -> CompanyState:
    """Dynamic Task Decomposition: breaks down failing components when retry limit is exceeded."""
    req = state["user_requirement"]
    err = state.get("error_message") or "Unknown persistent test failure"

    decomposed_plan = f"""# Task Decomposition Plan
## Context
Requirement: {req}
Component repeatedly failed verification ({state.get("retry_count")} retries).
Error diagnosis: {err[:200]}

## Decomposed Action Items
1. Isolate atomic parsing from arithmetic evaluation.
2. Refactor `solve_arithmetic` to handle whitespace and basic addition cleanly.
3. Reset verification harness for sub-component testing.
"""
    # Reset retries for the decomposed subtask and clear error message
    updated_logs = append_execution_log(
        state.get("execution_logs") or [],
        agent="task_decomposer",
        phase=PHASE_DECOMPOSE,
        message="Tech Lead: Max retries exceeded. Decomposed monolithic component into subtasks.",
        details={"decomposed_items_count": 3},
    )

    return {
        **state,
        "architecture_spec": f"{state.get('architecture_spec')}\n\n{decomposed_plan}",
        "retry_count": 0,
        "error_message": None,
        "current_phase": PHASE_CODER,
        "execution_logs": updated_logs,
    }


# --------------------------------------------------------------------------- #
# Routing Functions (Conditional Edges)
# --------------------------------------------------------------------------- #
def route_after_ceo(state: CompanyState) -> str:
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


def make_route_after_qa(max_retries: int = 3) -> Callable[[CompanyState], str]:
    def route_after_qa(state: CompanyState) -> str:
        # If test passed
        test_results = state.get("test_results")
        if state.get("current_phase") == PHASE_COMPLETED or (
            test_results is not None and "PASSED" in test_results
        ):
            return END

        # If tests failed
        retries = state.get("retry_count", 0)
        if retries >= max_retries:
            # Exceeded retries -> trigger task decomposer
            return "task_decomposer"
        # Otherwise -> route back to coder for self-correction with traceback
        return "coder"

    return route_after_qa


# --------------------------------------------------------------------------- #
# Workflow Assembler
# --------------------------------------------------------------------------- #
def create_company_workflow(
    project_root_getter: Callable[[str], Path] | None = None,
    max_retries: int | None = None,
    simulate_initial_bug: bool = False,
) -> StateGraph[Any, Any, Any, Any]:
    """Construct the LangGraph StateGraph with nodes and conditional edges."""
    cfg = get_config()
    retries = max_retries if max_retries is not None else cfg.workflow.max_retries

    workflow: StateGraph[Any, Any, Any, Any] = StateGraph(CompanyState)

    # 1. Register nodes
    workflow.add_node("ceo_router", cast(Any, ceo_router_node))
    workflow.add_node("architect", cast(Any, architect_node))
    workflow.add_node(
        "coder",
        cast(
            Any,
            make_coder_node(project_root_getter, simulate_initial_bug=simulate_initial_bug),
        ),
    )
    workflow.add_node("devops", cast(Any, make_devops_node(project_root_getter)))
    workflow.add_node("qa", cast(Any, make_qa_node(project_root_getter)))
    workflow.add_node("task_decomposer", cast(Any, task_decomposer_node))

    # 2. Set Entry Point
    workflow.set_entry_point("ceo_router")

    # 3. Add Edges
    workflow.add_conditional_edges(
        "ceo_router",
        route_after_ceo,
        {
            "architect": "architect",
            "coder": "coder",
            "devops": "devops",
            "qa": "qa",
            END: END,
        },
    )

    workflow.add_edge("architect", "coder")
    workflow.add_edge("coder", "devops")
    workflow.add_edge("devops", "qa")

    # 4. Conditional Edge after QA (Self-Correction & Task Decomposition)
    workflow.add_conditional_edges(
        "qa",
        make_route_after_qa(max_retries=retries),
        {
            "coder": "coder",
            "task_decomposer": "task_decomposer",
            END: END,
        },
    )

    # 5. After task decomposition, route to coder to implement decomposed spec
    workflow.add_edge("task_decomposer", "coder")

    return workflow


def compile_company_workflow(
    checkpointer: Any | None = None,
    project_root_getter: Callable[[str], Path] | None = None,
    max_retries: int | None = None,
    simulate_initial_bug: bool = False,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the LangGraph workflow ready for execution."""
    workflow = create_company_workflow(
        project_root_getter=project_root_getter,
        max_retries=max_retries,
        simulate_initial_bug=simulate_initial_bug,
    )
    chk = checkpointer if checkpointer is not None else JsonStateCheckpointer()
    return workflow.compile(checkpointer=chk)
