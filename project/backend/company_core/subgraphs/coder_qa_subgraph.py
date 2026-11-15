"""Code-Test-Fix Sub-Graph for LangGraph Orchestration.

Executes source code synthesis, test harness environment setup, automated test execution,
and self-correction loops when assertions fail.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from app.application.tools.registry import DynamicToolRegistry
from app.application.tools.retriever import ToolRetriever
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from company_core.agents.devops_engineer import DevOpsEngineerAgent
from company_core.agents.quality_assurance import QualityAssuranceAgent
from company_core.agents.software_engineer import SoftwareEngineerAgent
from company_core.state import (
    PHASE_CODER,
    PHASE_DEVOPS,
    PHASE_QA,
    CompanyState,
    GraphState,
    append_execution_log,
)


def _resolve_project_dir(
    project_id: str,
    resolver: Callable[[str], Path] | None = None,
) -> Path:
    if resolver:
        return resolver(project_id)
    base = Path.cwd() / "projects" / project_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def make_coder_node(
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    simulate_initial_bug: bool = False,
) -> Callable[[GraphState], GraphState]:
    """Create coder_node with JIT tool discovery and self-correction capability."""

    def coder_node(state: GraphState) -> GraphState:
        project_dir = _resolve_project_dir(
            state.get("project_id", "vibe-app"), project_root_resolver
        )
        retry_count = state.get("retry_count", 0)
        has_error = bool(state.get("error_message"))

        # JIT Dynamic Tool Retrieval: query and inject only relevant tool schemas
        active_tools_meta: list[dict[str, Any]] = []
        if tool_registry:
            retriever = ToolRetriever(tool_registry)
            # Query relevant coder tools (e.g. file writing / inspection)
            tool_matches = retriever.search_available_tools("file write view edit")
            active_tools_meta = [
                {"name": t["name"], "category": t["category"], "summary": t["summary"]}
                for t in tool_matches
            ]

        # Use SoftwareEngineerAgent for code generation and self-correction
        agent = SoftwareEngineerAgent(
            project_root_resolver=lambda _: project_dir,
            simulate_initial_bug=simulate_initial_bug,
        )

        existing_files = state.get("code_files") or {}
        req = state.get("user_requirement", "")

        if has_error and retry_count > 0:
            error_msg = state.get("error_message") or ""
            code_files = agent.apply_self_correction(req, error_context=error_msg)
            msg = (
                f"Coder: Applying self-correction patch based on QA traceback "
                f"(Attempt #{retry_count})."
            )
        else:
            code_files = agent.synthesize_code(req, simulate_bug=simulate_initial_bug)
            msg = "Coder: Implemented core source files and test fixtures."

        # Merge code files and persist to workspace
        merged_files = {**existing_files, **code_files}
        from company_core.tools import write_project_files

        write_project_files(project_dir, merged_files)

        completed = list(state.get("completed_tasks") or [])
        if "code" not in completed:
            completed.append("code")

        logs = append_execution_log(
            state.get("execution_logs") or [],
            agent="coder",
            phase=PHASE_CODER,
            message=msg,
            details={"written_files": list(code_files.keys()), "attempt": retry_count},
        )

        sub_results = dict(state.get("subgraph_results") or {})
        sub_results["coder"] = {
            "written_files": list(code_files.keys()),
            "retry_count": retry_count,
            "status": "ready_for_test",
        }

        return {
            **state,
            "code_files": merged_files,
            "completed_tasks": completed,
            "active_tools": active_tools_meta,
            "subgraph_results": sub_results,
            "current_node": "coder_node",
            "current_phase": PHASE_DEVOPS,
            "execution_logs": logs,
        }

    return coder_node


def make_devops_test_node(
    project_root_resolver: Callable[[str], Path] | None = None,
) -> Callable[[GraphState], GraphState]:
    """Create devops_test_node configuring requirements and pytest runner harness."""

    def devops_test_node(state: GraphState) -> GraphState:
        project_dir = _resolve_project_dir(
            state.get("project_id", "vibe-app"), project_root_resolver
        )
        agent = DevOpsEngineerAgent(project_root_resolver=lambda _: project_dir)

        # Build execution state for DevOps agent
        updated = agent.execute(state)

        logs = append_execution_log(
            updated.get("execution_logs") or [],
            agent="devops",
            phase=PHASE_DEVOPS,
            message="DevOps: Configured requirements.txt and pytest execution harness.",
        )

        sub_results = dict(updated.get("subgraph_results") or {})
        sub_results["devops"] = {"status": "configured"}

        return {
            **updated,
            "current_node": "devops_test_node",
            "current_phase": PHASE_QA,
            "subgraph_results": sub_results,
            "execution_logs": logs,
        }

    return devops_test_node


def make_evaluator_node(
    project_root_resolver: Callable[[str], Path] | None = None,
    timeout_s: float = 30.0,
) -> Callable[[GraphState], GraphState]:
    """Create evaluator_node executing pytest suite and validating test results."""

    def evaluator_node(state: GraphState) -> GraphState:
        project_dir = _resolve_project_dir(
            state.get("project_id", "vibe-app"), project_root_resolver
        )
        agent = QualityAssuranceAgent(
            project_root_resolver=lambda _: project_dir,
            timeout_seconds=timeout_s,
        )

        evaluated = agent.execute(state)

        test_results = evaluated.get("test_results") or ""
        passed = "PASSED" in test_results

        sub_results = dict(evaluated.get("subgraph_results") or {})
        sub_results["qa"] = {
            "passed": passed,
            "test_summary": test_results.splitlines()[0] if test_results else "No output",
            "retry_count": evaluated.get("retry_count", 0),
        }

        return {
            **evaluated,
            "current_node": "evaluator_node",
            "subgraph_results": sub_results,
        }

    return evaluator_node


def make_coder_qa_route(max_retries: int = 3) -> Callable[[GraphState], str]:
    """Evaluate test results to decide between self-correction loop vs exit."""

    def route_after_evaluator(state: GraphState) -> str:
        test_results = state.get("test_results") or ""
        passed = "PASSED" in test_results

        if passed:
            return END

        retries = state.get("retry_count", 0)
        limit = state.get("max_retries", max_retries)
        if retries < limit:
            # Self-Correction Loop: return to coder with error context and traceback
            return "coder_node"

        # Retry limit reached: exit sub-graph so parent graph or task decomposer can handle
        return END

    return route_after_evaluator


def create_coder_qa_subgraph(
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int = 3,
) -> StateGraph[Any, Any, Any, Any]:
    """Construct the Code-Test-Fix Sub-Graph with self-correction looping."""
    subgraph: StateGraph[Any, Any, Any, Any] = StateGraph(CompanyState)

    subgraph.add_node(
        "coder_node",
        cast(
            Any,
            make_coder_node(
                project_root_resolver=project_root_resolver,
                tool_registry=tool_registry,
                simulate_initial_bug=simulate_initial_bug,
            ),
        ),
    )
    subgraph.add_node("devops_test_node", cast(Any, make_devops_test_node(project_root_resolver)))
    subgraph.add_node("evaluator_node", cast(Any, make_evaluator_node(project_root_resolver)))

    subgraph.set_entry_point("coder_node")
    subgraph.add_edge("coder_node", "devops_test_node")
    subgraph.add_edge("devops_test_node", "evaluator_node")

    # Conditional Edge: Self-Correction loop on failure vs exit
    subgraph.add_conditional_edges(
        "evaluator_node",
        make_coder_qa_route(max_retries=max_retries),
        {
            "coder_node": "coder_node",
            END: END,
        },
    )

    return subgraph


def compile_coder_qa_subgraph(
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int = 3,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the Code-Test-Fix Sub-Graph ready for execution."""
    subgraph = create_coder_qa_subgraph(
        project_root_resolver=project_root_resolver,
        tool_registry=tool_registry,
        simulate_initial_bug=simulate_initial_bug,
        max_retries=max_retries,
    )
    return subgraph.compile()


__all__ = [
    "compile_coder_qa_subgraph",
    "create_coder_qa_subgraph",
    "make_coder_node",
    "make_coder_qa_route",
    "make_devops_test_node",
    "make_evaluator_node",
]
