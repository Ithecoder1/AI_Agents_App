"""Builder-pattern assembler and compiler for the Master Orchestrator StateGraph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.application.tools.registry import DynamicToolRegistry
from app.application.triage import CascadingTriageService, get_triage_service
from app.domain.ports import (
    ICodeWorkspaceRunner,
    IOrchestratorCheckpointer,
    IProfileClassifier,
)
from app.domain.ports.agent_provider import IAgentProvider
from app.infrastructure.orchestration.adapters import LocalCodeWorkspaceRunner
from app.infrastructure.orchestration.graph.routing import MasterGraphRouter
from app.infrastructure.orchestration.nodes import (
    ExecutorNode,
    PlannerNode,
    RouterNode,
    TaskDecomposerNode,
    TriageNode,
)
from app.infrastructure.orchestration.persistence import JsonStateCheckpointer
from app.infrastructure.orchestration.state import (
    CompanyState,
    ExecutionLogManager,
    OrchestratorConfigLoader,
    SkillRegistry,
    TriageProfileResolver,
)
from app.infrastructure.orchestration.subgraphs import (
    ArchitectSubGraphBuilder,
    BaseSubGraphBuilder,
    CoderQASubGraphBuilder,
    TaskDecompositionStrategy,
)


class MasterGraphBuilder:
    """Assembles and compiles the dynamic intent-driven Master Orchestrator ``StateGraph``."""

    def __init__(
        self,
        *,
        checkpointer: IOrchestratorCheckpointer | None = None,
        workspace_runner: ICodeWorkspaceRunner | None = None,
        profile_classifier: IProfileClassifier | None = None,
        skill_registry: SkillRegistry | None = None,
        tool_registry: DynamicToolRegistry | None = None,
        triage_service: CascadingTriageService | None = None,
        profile_resolver: TriageProfileResolver | None = None,
        provider: IAgentProvider | None = None,
        log_manager: ExecutionLogManager | None = None,
        router: MasterGraphRouter | None = None,
        decomposition_strategy: TaskDecompositionStrategy | None = None,
        architect_subgraph_factory: Callable[[], CompiledStateGraph[Any, Any, Any, Any]]
        | None = None,
        coder_qa_subgraph_factory: Callable[[], CompiledStateGraph[Any, Any, Any, Any]]
        | None = None,
        simulate_initial_bug: bool = False,
        max_retries: int | None = None,
    ) -> None:
        self._checkpointer = checkpointer
        self._workspace_runner = workspace_runner
        self._profile_classifier: IProfileClassifier = (
            profile_classifier
            if profile_classifier is not None
            else OrchestratorConfigLoader.default()
        )
        self._skill_registry = (
            skill_registry if skill_registry is not None else SkillRegistry.default()
        )
        self._tool_registry = tool_registry
        self._triage_service = (
            triage_service if triage_service is not None else get_triage_service()
        )
        self._profile_resolver = profile_resolver
        self._provider = provider
        self._log_manager = log_manager if log_manager is not None else ExecutionLogManager()
        self._decomposition_strategy = (
            decomposition_strategy
            if decomposition_strategy is not None
            else TaskDecompositionStrategy()
        )
        self._architect_subgraph_factory = architect_subgraph_factory
        self._coder_qa_subgraph_factory = coder_qa_subgraph_factory
        self._simulate_initial_bug = simulate_initial_bug

        default_retries = (
            self._profile_classifier.workflow.max_retries
            if isinstance(self._profile_classifier, OrchestratorConfigLoader)
            else 3
        )
        self._max_retries = max_retries if max_retries is not None else default_retries
        self._router = (
            router
            if router is not None
            else MasterGraphRouter(default_max_retries=self._max_retries)
        )

        self._triage_node = TriageNode(
            profile_classifier=self._profile_classifier,
            skill_registry=self._skill_registry,
            log_manager=self._log_manager,
            tool_registry=self._tool_registry,
            triage_service=self._triage_service,
            profile_resolver=self._profile_resolver,
        )
        self._planner_node = PlannerNode(
            profile_classifier=self._profile_classifier,
            log_manager=self._log_manager,
        )
        self._router_node = RouterNode(log_manager=self._log_manager)
        self._executor_node = ExecutorNode(
            log_manager=self._log_manager,
            provider=self._provider,
        )
        self._task_decomposer_node = TaskDecomposerNode(
            decomposition_strategy=self._decomposition_strategy,
            log_manager=self._log_manager,
        )

    @property
    def triage_node(self) -> TriageNode:
        """Configured ``TriageNode`` command instance."""
        return self._triage_node

    @property
    def planner_node(self) -> PlannerNode:
        """Configured ``PlannerNode`` command instance."""
        return self._planner_node

    @property
    def router_node(self) -> RouterNode:
        """Configured ``RouterNode`` command instance."""
        return self._router_node

    @property
    def executor_node(self) -> ExecutorNode:
        """Configured ``ExecutorNode`` command instance."""
        return self._executor_node

    @property
    def task_decomposer_node(self) -> TaskDecomposerNode:
        """Configured ``TaskDecomposerNode`` command instance."""
        return self._task_decomposer_node

    @property
    def router(self) -> MasterGraphRouter:
        """Configured ``MasterGraphRouter`` conditional routing policy."""
        return self._router

    @property
    def checkpointer(self) -> IOrchestratorCheckpointer | None:
        """Configured ``IOrchestratorCheckpointer``, if provided."""
        return self._checkpointer

    def _compile_architect_subgraph(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        if self._architect_subgraph_factory is not None:
            return self._architect_subgraph_factory()
        return ArchitectSubGraphBuilder(log_manager=self._log_manager).compile()

    def _compile_coder_qa_subgraph(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        if self._coder_qa_subgraph_factory is not None:
            return self._coder_qa_subgraph_factory()
        runner = (
            self._workspace_runner
            if self._workspace_runner is not None
            else LocalCodeWorkspaceRunner()
        )
        return CoderQASubGraphBuilder(
            workspace_runner=runner,
            log_manager=self._log_manager,
            tool_registry=self._tool_registry,
            simulate_initial_bug=self._simulate_initial_bug,
            max_retries=self._max_retries,
        ).compile()

    def build(self) -> StateGraph[Any, Any, Any, Any]:
        """Build the Master ``StateGraph`` wiring nodes, sub-graphs, and conditional edges."""
        workflow: StateGraph[Any, Any, Any, Any] = StateGraph(CompanyState)

        BaseSubGraphBuilder._register_node(workflow, self._triage_node)
        BaseSubGraphBuilder._register_node(workflow, self._planner_node)
        BaseSubGraphBuilder._register_node(workflow, self._router_node)
        BaseSubGraphBuilder._register_node(workflow, self._executor_node)
        BaseSubGraphBuilder._register_node(workflow, self._task_decomposer_node)

        workflow.add_node("architect_subgraph", cast("Any", self._compile_architect_subgraph()))
        workflow.add_node("coder_qa_subgraph", cast("Any", self._compile_coder_qa_subgraph()))

        workflow.set_entry_point(self._triage_node.name)

        workflow.add_conditional_edges(
            self._triage_node.name,
            self._router.route_after_triage,
            {
                self._executor_node.name: self._executor_node.name,
                self._planner_node.name: self._planner_node.name,
            },
        )
        workflow.add_conditional_edges(
            self._planner_node.name,
            self._router.route_after_planner,
            {
                self._executor_node.name: self._executor_node.name,
                self._router_node.name: self._router_node.name,
                END: END,
            },
        )
        workflow.add_conditional_edges(
            self._router_node.name,
            self._router.route_after_router,
            {
                "architect_subgraph": "architect_subgraph",
                "coder_qa_subgraph": "coder_qa_subgraph",
                self._executor_node.name: self._executor_node.name,
                END: END,
            },
        )
        workflow.add_conditional_edges(
            "architect_subgraph",
            self._router.route_after_architect,
            {
                "coder_qa_subgraph": "coder_qa_subgraph",
                END: END,
            },
        )
        workflow.add_conditional_edges(
            "coder_qa_subgraph",
            self._router.route_after_coder_qa,
            {
                self._task_decomposer_node.name: self._task_decomposer_node.name,
                END: END,
            },
        )
        workflow.add_edge(self._task_decomposer_node.name, "coder_qa_subgraph")
        workflow.add_edge(self._executor_node.name, END)
        return workflow

    def compile(
        self,
        *,
        checkpointer: Any | None = None,
    ) -> CompiledStateGraph[Any, Any, Any, Any]:
        """Compile the Master ``StateGraph`` with the configured or fallback checkpointer."""
        chk: Any = (
            checkpointer
            if checkpointer is not None
            else (self._checkpointer if self._checkpointer is not None else JsonStateCheckpointer())
        )
        return self.build().compile(checkpointer=chk)


def build_master_graph(
    *,
    checkpointer: IOrchestratorCheckpointer | None = None,
    workspace_runner: ICodeWorkspaceRunner | None = None,
    profile_classifier: IProfileClassifier | None = None,
    skill_registry: SkillRegistry | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    triage_service: CascadingTriageService | None = None,
    profile_resolver: TriageProfileResolver | None = None,
    provider: IAgentProvider | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int | None = None,
    architect_subgraph_factory: Callable[[], CompiledStateGraph[Any, Any, Any, Any]] | None = None,
    coder_qa_subgraph_factory: Callable[[], CompiledStateGraph[Any, Any, Any, Any]] | None = None,
) -> StateGraph[Any, Any, Any, Any]:
    """Construct an uncompiled Master ``StateGraph`` via ``MasterGraphBuilder``."""
    return MasterGraphBuilder(
        checkpointer=checkpointer,
        workspace_runner=workspace_runner,
        profile_classifier=profile_classifier,
        skill_registry=skill_registry,
        tool_registry=tool_registry,
        triage_service=triage_service,
        profile_resolver=profile_resolver,
        provider=provider,
        simulate_initial_bug=simulate_initial_bug,
        max_retries=max_retries,
        architect_subgraph_factory=architect_subgraph_factory,
        coder_qa_subgraph_factory=coder_qa_subgraph_factory,
    ).build()


def compile_master_graph(
    *,
    checkpointer: Any | None = None,
    workspace_runner: ICodeWorkspaceRunner | None = None,
    profile_classifier: IProfileClassifier | None = None,
    skill_registry: SkillRegistry | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    triage_service: CascadingTriageService | None = None,
    profile_resolver: TriageProfileResolver | None = None,
    provider: IAgentProvider | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int | None = None,
    architect_subgraph_factory: Callable[[], CompiledStateGraph[Any, Any, Any, Any]] | None = None,
    coder_qa_subgraph_factory: Callable[[], CompiledStateGraph[Any, Any, Any, Any]] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the Master ``StateGraph`` via ``MasterGraphBuilder``."""
    builder = MasterGraphBuilder(
        checkpointer=checkpointer if isinstance(checkpointer, IOrchestratorCheckpointer) else None,
        workspace_runner=workspace_runner,
        profile_classifier=profile_classifier,
        skill_registry=skill_registry,
        tool_registry=tool_registry,
        triage_service=triage_service,
        profile_resolver=profile_resolver,
        provider=provider,
        simulate_initial_bug=simulate_initial_bug,
        max_retries=max_retries,
        architect_subgraph_factory=architect_subgraph_factory,
        coder_qa_subgraph_factory=coder_qa_subgraph_factory,
    )
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "MasterGraphBuilder",
    "build_master_graph",
    "compile_master_graph",
]
