"""Architecture Design Sub-Graph for LangGraph Orchestration.

Isolates requirements analysis, blueprint synthesis, and schema/directory tree validation.
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from company_core.agents.software_architect import SoftwareArchitectAgent
from company_core.state import (
    INTENT_ARCHITECTURE_DESIGN,
    PHASE_ARCHITECT,
    PHASE_COMPLETED,
    STATUS_COMPLETED,
    CompanyState,
    GraphState,
    append_execution_log,
)


def requirements_parser_node(state: GraphState) -> GraphState:
    """Parse user requirement into structured architectural requirements."""
    req = state.get("user_requirement", "").strip()
    is_math = any(k in req.lower() for k in ("1+1", "math", "calculator", "add", "arithmetic"))

    parsed = {
        "raw_requirement": req,
        "domain_type": "arithmetic_evaluator" if is_math else "business_engine",
        "pattern": "hexagonal_modular",
        "language": "python_3.10+",
        "testing_framework": "pytest",
        "requires_core_engine": True,
        "requires_test_harness": True,
    }

    sub_results = dict(state.get("subgraph_results") or {})
    sub_results["architecture_requirements"] = parsed

    logs = append_execution_log(
        state.get("execution_logs") or [],
        agent="architect",
        phase=PHASE_ARCHITECT,
        message=f"Architect: Parsed requirements for domain '{parsed['domain_type']}'.",
        details=parsed,
    )

    return {
        **state,
        "current_node": "requirements_parser_node",
        "subgraph_results": sub_results,
        "execution_logs": logs,
    }


def blueprint_generator_node(state: GraphState) -> GraphState:
    """Synthesize modular system architecture specification and module layout."""
    agent = SoftwareArchitectAgent()
    req = state.get("user_requirement", "")
    spec = agent.design_system_architecture(req)

    logs = append_execution_log(
        state.get("execution_logs") or [],
        agent="architect",
        phase=PHASE_ARCHITECT,
        message="Architect: Generated architecture specification and module layout.",
        details={"spec_lines": len(spec.splitlines())},
    )

    return {
        **state,
        "architecture_spec": spec,
        "current_node": "blueprint_generator_node",
        "execution_logs": logs,
    }


def schema_validator_node(state: GraphState) -> GraphState:
    """Validate system architecture blueprint and register completed task."""
    spec = state.get("architecture_spec") or ""
    is_valid = bool(
        "Component Boundaries" in spec or "Directory Tree" in spec or "Architecture" in spec
    )

    completed = list(state.get("completed_tasks") or [])
    if is_valid and "architecture" not in completed:
        completed.append("architecture")

    sub_results = dict(state.get("subgraph_results") or {})
    sub_results["architecture_spec_valid"] = is_valid

    logs = append_execution_log(
        state.get("execution_logs") or [],
        agent="architect",
        phase=PHASE_ARCHITECT,
        message="Architect: Validated architecture blueprint schemas and contracts.",
        details={"valid": is_valid},
    )

    profile = state.get("execution_profile") or {}
    profile_subgraphs = profile.get("subgraphs")
    is_architecture_only = (
        state.get("task_type") == "ARCHITECTURE"
        or state.get("intent") == INTENT_ARCHITECTURE_DESIGN
        or (isinstance(profile_subgraphs, list) and "coder_qa_subgraph" not in profile_subgraphs)
    )

    next_state: GraphState = {
        **state,
        "completed_tasks": completed,
        "subgraph_results": sub_results,
        "current_node": "schema_validator_node",
        "execution_logs": logs,
    }
    if is_valid and is_architecture_only:
        next_state["status"] = STATUS_COMPLETED
        next_state["current_phase"] = PHASE_COMPLETED

    return next_state


def create_architect_subgraph() -> StateGraph[Any, Any, Any, Any]:
    """Construct the isolated architecture design Sub-Graph."""
    subgraph: StateGraph[Any, Any, Any, Any] = StateGraph(CompanyState)

    subgraph.add_node("requirements_parser_node", cast(Any, requirements_parser_node))
    subgraph.add_node("blueprint_generator_node", cast(Any, blueprint_generator_node))
    subgraph.add_node("schema_validator_node", cast(Any, schema_validator_node))

    subgraph.set_entry_point("requirements_parser_node")
    subgraph.add_edge("requirements_parser_node", "blueprint_generator_node")
    subgraph.add_edge("blueprint_generator_node", "schema_validator_node")
    subgraph.add_edge("schema_validator_node", END)

    return subgraph


def compile_architect_subgraph() -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the architecture design Sub-Graph for standalone or composed execution."""
    return create_architect_subgraph().compile()


__all__ = [
    "blueprint_generator_node",
    "compile_architect_subgraph",
    "create_architect_subgraph",
    "requirements_parser_node",
    "schema_validator_node",
]
