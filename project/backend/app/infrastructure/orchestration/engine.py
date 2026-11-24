"""Public OOP execution engine and dependency container for LangGraph orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.application.tools.registry import DynamicToolRegistry
from app.application.triage import CascadingTriageService, get_triage_service
from app.domain.graph_state import GraphState, create_initial_graph_state
from app.domain.ports import (
    ICodeWorkspaceRunner,
    IOrchestratorCheckpointer,
    IOrchestratorEngine,
    IProfileClassifier,
)
from app.domain.ports.agent_provider import IAgentProvider
from app.infrastructure.orchestration.adapters import LocalCodeWorkspaceRunner
from app.infrastructure.orchestration.graph import MasterGraphBuilder
from app.infrastructure.orchestration.persistence import GraphStateHydrator, JsonStateCheckpointer
from app.infrastructure.orchestration.state import (
    ExecutionLogManager,
    OrchestratorConfigLoader,
    SkillRegistry,
)


@dataclass(frozen=True, slots=True)
class OrchestratorDependencies:
    """Explicit DI container bundling collaborators for :class:`OrchestratorEngine`."""

    checkpointer: IOrchestratorCheckpointer | None = None
    hydrator: GraphStateHydrator | None = None
    workspace_runner: ICodeWorkspaceRunner | None = None
    profile_classifier: IProfileClassifier | None = None
    skill_registry: SkillRegistry | None = None
    tool_registry: DynamicToolRegistry | None = None
    triage_service: CascadingTriageService | None = None
    log_manager: ExecutionLogManager | None = None
    architect_subgraph_factory: Callable[[], CompiledStateGraph[Any, Any, Any, Any]] | None = None
    coder_qa_subgraph_factory: Callable[[], CompiledStateGraph[Any, Any, Any, Any]] | None = None


class _PinnedDirectoryWorkspaceRunner(LocalCodeWorkspaceRunner):
    """Workspace adapter pinning project resolution strictly to a configured root directory."""

    def __init__(self, pinned_dir: Path) -> None:
        self._pinned_dir = pinned_dir.resolve()
        super().__init__(base_workspace_dir=self._pinned_dir)

    def resolve_project_dir(self, project_id: str) -> Path:
        _ = project_id
        self._pinned_dir.mkdir(parents=True, exist_ok=True)
        return self._pinned_dir


class OrchestratorEngine:
    """Facade encapsulating master graph instantiation, state hydration, and sync/async runs."""

    def __init__(
        self,
        *,
        dependencies: OrchestratorDependencies | None = None,
        checkpoint_path: Path | str | None = None,
        max_retries: int | None = None,
        simulate_initial_bug: bool = False,
    ) -> None:
        deps = dependencies or OrchestratorDependencies()
        self._profile_classifier: IProfileClassifier = (
            deps.profile_classifier
            if deps.profile_classifier is not None
            else OrchestratorConfigLoader.default()
        )
        default_ckpt_str = (
            self._profile_classifier.workflow.checkpoint_path
            if isinstance(self._profile_classifier, OrchestratorConfigLoader)
            else "logs/company_state.json"
        )
        default_retries = (
            self._profile_classifier.workflow.max_retries
            if isinstance(self._profile_classifier, OrchestratorConfigLoader)
            else 3
        )
        self._checkpoint_path = Path(checkpoint_path or default_ckpt_str)
        self._max_retries = max_retries if max_retries is not None else default_retries
        self._simulate_initial_bug = simulate_initial_bug
        self._log_manager = (
            deps.log_manager if deps.log_manager is not None else ExecutionLogManager()
        )
        self._hydrator = (
            deps.hydrator
            if deps.hydrator is not None
            else GraphStateHydrator(
                default_workspace_dir=self._checkpoint_path.parent,
                log_manager=self._log_manager,
            )
        )
        self._checkpointer: IOrchestratorCheckpointer = (
            deps.checkpointer
            if deps.checkpointer is not None
            else JsonStateCheckpointer(log_path=self._checkpoint_path, hydrator=self._hydrator)
        )
        self._workspace_runner = deps.workspace_runner
        self._skill_registry = (
            deps.skill_registry if deps.skill_registry is not None else SkillRegistry.default()
        )
        self._tool_registry = deps.tool_registry
        self._triage_service = (
            deps.triage_service if deps.triage_service is not None else get_triage_service()
        )
        self._architect_subgraph_factory = deps.architect_subgraph_factory
        self._coder_qa_subgraph_factory = deps.coder_qa_subgraph_factory

    @property
    def checkpoint_path(self) -> Path:
        """Filesystem path used for JSON state checkpointing."""
        return self._checkpoint_path

    @property
    def hydrator(self) -> GraphStateHydrator:
        """Configured :class:`GraphStateHydrator` instance."""
        return self._hydrator

    @property
    def checkpointer(self) -> IOrchestratorCheckpointer:
        """Configured :class:`IOrchestratorCheckpointer` instance."""
        return self._checkpointer

    def prepare_initial_state(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        *,
        resume_if_possible: bool = True,
        reset_state: bool = False,
    ) -> GraphState:
        """Load and hydrate an interrupted checkpoint or initialize a fresh ``GraphState``."""
        if reset_state and self._checkpoint_path.is_file():
            self._checkpoint_path.unlink()

        persisted = self._hydrator.load(self._checkpoint_path) if resume_if_possible else None
        if persisted is not None and self._hydrator.can_resume(persisted):
            return self._hydrator.hydrate(persisted)

        return create_initial_graph_state(
            user_requirement=user_requirement,
            project_id=project_id,
            max_retries=self._max_retries,
        )

    def compile_master_graph(
        self,
        *,
        provider: IAgentProvider | None = None,
        tool_registry: DynamicToolRegistry | None = None,
    ) -> CompiledStateGraph[Any, Any, Any, Any]:
        """Instantiate and compile the master workflow via :class:`MasterGraphBuilder`."""
        resolved_tools = tool_registry if tool_registry is not None else self._tool_registry
        builder = MasterGraphBuilder(
            checkpointer=self._checkpointer,
            workspace_runner=self._workspace_runner,
            profile_classifier=self._profile_classifier,
            skill_registry=self._skill_registry,
            tool_registry=resolved_tools,
            triage_service=self._triage_service,
            provider=provider,
            log_manager=self._log_manager,
            architect_subgraph_factory=self._architect_subgraph_factory,
            coder_qa_subgraph_factory=self._coder_qa_subgraph_factory,
            simulate_initial_bug=self._simulate_initial_bug,
            max_retries=self._max_retries,
        )
        return builder.compile()

    def invoke(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        *,
        provider: IAgentProvider | None = None,
        tool_registry: DynamicToolRegistry | None = None,
        resume_if_possible: bool = True,
        reset_state: bool = False,
    ) -> GraphState:
        """Synchronously prepare state and invoke the compiled master graph to completion."""
        initial_state = self.prepare_initial_state(
            user_requirement=user_requirement,
            project_id=project_id,
            resume_if_possible=resume_if_possible,
            reset_state=reset_state,
        )
        compiled = self.compile_master_graph(provider=provider, tool_registry=tool_registry)
        config: RunnableConfig = {"configurable": {"thread_id": project_id}}
        return cast("GraphState", compiled.invoke(initial_state, config=config))

    async def astream(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        *,
        resume_if_possible: bool = True,
        reset_state: bool = False,
        tool_registry: DynamicToolRegistry | None = None,
        provider: IAgentProvider | None = None,
    ) -> AsyncIterator[tuple[str, GraphState]]:
        """Asynchronously stream ``(node_name, state_snapshot)`` updates from the master graph."""
        initial_state = await asyncio.to_thread(
            self.prepare_initial_state,
            user_requirement,
            project_id,
            resume_if_possible=resume_if_possible,
            reset_state=reset_state,
        )
        compiled = self.compile_master_graph(provider=provider, tool_registry=tool_registry)
        config: RunnableConfig = {"configurable": {"thread_id": project_id}}

        async for chunk in compiled.astream(initial_state, config=config, stream_mode="updates"):
            if isinstance(chunk, dict):
                for node_name, state_update in chunk.items():
                    if isinstance(state_update, dict):
                        yield str(node_name), cast("GraphState", state_update)

    def run_master_workflow(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        provider: IAgentProvider | None = None,
        tool_registry: Any | None = None,
        resume_if_possible: bool = True,
        reset_state: bool = False,
    ) -> GraphState:
        """``IOrchestratorEngine`` contract method delegating to :meth:`invoke`."""
        return self.invoke(
            user_requirement=user_requirement,
            project_id=project_id,
            provider=provider,
            tool_registry=cast("DynamicToolRegistry | None", tool_registry),
            resume_if_possible=resume_if_possible,
            reset_state=reset_state,
        )

    async def astream_nodes(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        resume_if_possible: bool = True,
        reset_state: bool = False,
        use_master_graph: bool = True,
        tool_registry: Any | None = None,
    ) -> AsyncIterator[tuple[str, GraphState]]:
        """``IOrchestratorEngine`` contract method streaming updates via :meth:`astream`."""
        _ = use_master_graph
        async for node_name, state_snapshot in self.astream(
            user_requirement=user_requirement,
            project_id=project_id,
            resume_if_possible=resume_if_possible,
            reset_state=reset_state,
            tool_registry=cast("DynamicToolRegistry | None", tool_registry),
        ):
            yield node_name, state_snapshot


class OrchestratorEngineFactory:
    """Factory creating workspace-pinned :class:`OrchestratorEngine` instances."""

    def __init__(
        self,
        *,
        profile_classifier: IProfileClassifier | None = None,
        skill_registry: SkillRegistry | None = None,
        tool_registry: DynamicToolRegistry | None = None,
        triage_service: CascadingTriageService | None = None,
    ) -> None:
        self._profile_classifier = profile_classifier
        self._skill_registry = skill_registry
        self._tool_registry = tool_registry
        self._triage_service = triage_service

    def create(
        self,
        *,
        workspace_dir: Path,
        checkpoint_path: Path,
        max_retries: int = 3,
    ) -> IOrchestratorEngine:
        """Create a dedicated :class:`OrchestratorEngine` pinned to ``workspace_dir``."""
        hydrator = GraphStateHydrator(default_workspace_dir=checkpoint_path.parent)
        checkpointer = JsonStateCheckpointer(log_path=checkpoint_path, hydrator=hydrator)
        deps = OrchestratorDependencies(
            checkpointer=checkpointer,
            hydrator=hydrator,
            workspace_runner=_PinnedDirectoryWorkspaceRunner(workspace_dir),
            profile_classifier=self._profile_classifier,
            skill_registry=self._skill_registry,
            tool_registry=self._tool_registry,
            triage_service=self._triage_service,
        )
        return OrchestratorEngine(
            dependencies=deps,
            checkpoint_path=checkpoint_path,
            max_retries=max_retries,
        )


__all__ = [
    "OrchestratorDependencies",
    "OrchestratorEngine",
    "OrchestratorEngineFactory",
]
