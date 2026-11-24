"""DEPRECATED: Compatibility shim for the OOP Master Orchestrator graph and Command nodes.

Prefer ``app.infrastructure.orchestration.MasterGraphBuilder`` and
``app.infrastructure.orchestration.nodes``.
"""

from __future__ import annotations

__deprecated__ = True

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from app.application.tools.registry import DynamicToolRegistry
from app.application.triage import CascadingTriageService, get_triage_service
from app.domain.ports.agent_provider import IAgentProvider
from app.infrastructure.orchestration.graph import (
    MasterGraphBuilder,
    MasterGraphRouter,
    build_master_graph,
    compile_master_graph,
    route_after_architect,
    route_after_coder_qa,
    route_after_planner,
    route_after_router,
    route_after_triage,
)
from app.infrastructure.orchestration.nodes import (
    ExecutorNode,
    PlannerNode,
    RouterNode,
    TaskDecomposerNode,
    TriageNode,
)
from app.infrastructure.orchestration.state import ExecutionLogManager
from app.infrastructure.orchestration.subgraphs.coder_qa import TaskDecompositionStrategy
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from company_core.config import ConfigManager, get_config
from company_core.persistence import JsonStateCheckpointer
from company_core.skills import SkillRegistry, get_default_skill_registry
from company_core.state import GraphState
from company_core.subgraphs.architect_subgraph import compile_architect_subgraph
from company_core.subgraphs.coder_qa_subgraph import compile_coder_qa_subgraph


def make_triage_node(
    tool_registry: DynamicToolRegistry | None = None,
    config: ConfigManager | None = None,
    skill_registry: SkillRegistry | None = None,
    triage_service: CascadingTriageService | None = None,
) -> Callable[[GraphState], GraphState]:
    """Create a synchronous ``triage_node`` callable delegating to ``TriageNode``."""
    cfg = config or get_config()
    skills = skill_registry or get_default_skill_registry()
    triage_svc = triage_service or get_triage_service()
    node = TriageNode(
        profile_classifier=cfg,
        skill_registry=skills,
        log_manager=ExecutionLogManager(),
        tool_registry=tool_registry,
        triage_service=triage_svc,
    )
    return cast("Callable[[GraphState], GraphState]", node.execute)


def make_planner_node(
    config: ConfigManager | None = None,
) -> Callable[[GraphState], GraphState]:
    """Create a synchronous ``planner_node`` callable delegating to ``PlannerNode``."""
    cfg = config or get_config()
    node = PlannerNode(profile_classifier=cfg, log_manager=ExecutionLogManager())
    return cast("Callable[[GraphState], GraphState]", node.execute)


def make_subgraph_router_node() -> Callable[[GraphState], GraphState]:
    """Create a synchronous ``subgraph_router_node`` callable delegating to ``RouterNode``."""
    node = RouterNode(log_manager=ExecutionLogManager())
    return cast("Callable[[GraphState], GraphState]", node.execute)


def make_executor_node(
    provider: IAgentProvider | None = None,
) -> Callable[[GraphState], GraphState]:
    """Create a synchronous ``executor_node`` callable delegating to ``ExecutorNode``."""
    node = ExecutorNode(log_manager=ExecutionLogManager(), provider=provider)
    return cast("Callable[[GraphState], GraphState]", node.execute)


def make_task_decomposer_node() -> Callable[[GraphState], GraphState]:
    """Create a synchronous ``task_decomposer`` callable delegating to ``TaskDecomposerNode``."""
    node = TaskDecomposerNode(
        decomposition_strategy=TaskDecompositionStrategy(),
        log_manager=ExecutionLogManager(),
    )
    return cast("Callable[[GraphState], GraphState]", node.execute)


def create_orchestrator_workflow(
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    provider: IAgentProvider | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int | None = None,
    config: ConfigManager | None = None,
    skill_registry: SkillRegistry | None = None,
    triage_service: CascadingTriageService | None = None,
) -> StateGraph[Any, Any, Any, Any]:
    """Construct the Master Orchestrator ``StateGraph`` via ``MasterGraphBuilder``."""
    cfg = config or get_config()
    skills = skill_registry or get_default_skill_registry()
    triage_svc = triage_service or get_triage_service()
    retries = max_retries if max_retries is not None else cfg.workflow.max_retries

    builder = MasterGraphBuilder(
        profile_classifier=cfg,
        skill_registry=skills,
        tool_registry=tool_registry,
        triage_service=triage_svc,
        provider=provider,
        simulate_initial_bug=simulate_initial_bug,
        max_retries=retries,
        architect_subgraph_factory=compile_architect_subgraph,
        coder_qa_subgraph_factory=lambda: compile_coder_qa_subgraph(
            project_root_resolver=project_root_resolver,
            tool_registry=tool_registry,
            simulate_initial_bug=simulate_initial_bug,
            max_retries=retries,
        ),
    )
    return builder.build()


def compile_orchestrator_workflow(
    checkpointer: Any | None = None,
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    provider: IAgentProvider | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int | None = None,
    config: ConfigManager | None = None,
    skill_registry: SkillRegistry | None = None,
    triage_service: CascadingTriageService | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the Master Orchestrator ``StateGraph`` with checkpoint persistence."""
    workflow = create_orchestrator_workflow(
        project_root_resolver=project_root_resolver,
        tool_registry=tool_registry,
        provider=provider,
        simulate_initial_bug=simulate_initial_bug,
        max_retries=max_retries,
        config=config,
        skill_registry=skill_registry,
        triage_service=triage_service,
    )
    chk = checkpointer if checkpointer is not None else JsonStateCheckpointer()
    return workflow.compile(checkpointer=chk)


__all__ = [
    "ExecutorNode",
    "MasterGraphBuilder",
    "MasterGraphRouter",
    "PlannerNode",
    "RouterNode",
    "TaskDecomposerNode",
    "TriageNode",
    "build_master_graph",
    "compile_master_graph",
    "compile_orchestrator_workflow",
    "create_orchestrator_workflow",
    "make_executor_node",
    "make_planner_node",
    "make_subgraph_router_node",
    "make_task_decomposer_node",
    "make_triage_node",
    "route_after_architect",
    "route_after_coder_qa",
    "route_after_planner",
    "route_after_router",
    "route_after_triage",
]
