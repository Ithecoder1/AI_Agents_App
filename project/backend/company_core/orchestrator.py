"""Autonomous Startup Orchestrator Engine.

Encapsulates LangGraph workflow assembly, multi-agent coordination, state persistence,
and real-time execution streaming.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from company_core.agents import (
    CeoOrchestratorAgent,
    DevOpsEngineerAgent,
    QualityAssuranceAgent,
    SoftwareArchitectAgent,
    SoftwareEngineerAgent,
    TaskDecomposerAgent,
)
from company_core.config import ConfigManager, get_config
from company_core.orchestrator_graph import (
    compile_orchestrator_workflow,
    create_orchestrator_workflow,
)
from company_core.persistence import (
    JsonStateCheckpointer,
    can_resume,
    hydrate_resumed_state,
    load_company_state,
)
from company_core.state import (
    CompanyState,
    create_initial_state,
)


class AutonomousStartupOrchestrator:
    """State-driven multi-agent orchestration engine for autonomous startups."""

    def __init__(
        self,
        *,
        ceo_agent: CeoOrchestratorAgent | None = None,
        architect_agent: SoftwareArchitectAgent | None = None,
        engineer_agent: SoftwareEngineerAgent | None = None,
        devops_agent: DevOpsEngineerAgent | None = None,
        qa_agent: QualityAssuranceAgent | None = None,
        decomposer_agent: TaskDecomposerAgent | None = None,
        checkpointer: JsonStateCheckpointer | None = None,
        checkpoint_path: Path | str | None = None,
        max_retries: int | None = None,
        project_root_resolver: Callable[[str], Path] | None = None,
        simulate_initial_bug: bool = False,
        config: ConfigManager | None = None,
    ) -> None:
        self.config = config or get_config()
        self.max_retries = (
            max_retries if max_retries is not None else self.config.workflow.max_retries
        )
        self.checkpoint_path = Path(checkpoint_path or self.config.workflow.checkpoint_path)
        self.checkpointer = checkpointer or JsonStateCheckpointer(log_path=self.checkpoint_path)
        self.project_root_resolver = project_root_resolver
        self.simulate_initial_bug = simulate_initial_bug

        # Initialize agent workers
        self.ceo_agent = ceo_agent or CeoOrchestratorAgent(config=self.config.get_agent("ceo"))
        self.architect_agent = architect_agent or SoftwareArchitectAgent(
            config=self.config.get_agent("architect")
        )
        self.engineer_agent = engineer_agent or SoftwareEngineerAgent(
            project_root_resolver=self.project_root_resolver,
            config=self.config.get_agent("coder"),
            simulate_initial_bug=self.simulate_initial_bug,
        )
        self.devops_agent = devops_agent or DevOpsEngineerAgent(
            project_root_resolver=self.project_root_resolver,
            config=self.config.get_agent("devops"),
        )
        self.qa_agent = qa_agent or QualityAssuranceAgent(
            project_root_resolver=self.project_root_resolver,
            config=self.config.get_agent("qa"),
        )
        self.decomposer_agent = decomposer_agent or TaskDecomposerAgent(
            config=self.config.get_agent("task_decomposer")
        )

        self._compiled_graph: CompiledStateGraph[Any, Any, Any, Any] | None = None

    def assemble_workflow(self) -> StateGraph[Any, Any, Any, Any]:
        """Construct the LangGraph StateGraph with nodes and conditional edges."""
        workflow: StateGraph[Any, Any, Any, Any] = StateGraph(CompanyState)

        # 1. Register agent worker nodes
        workflow.add_node("ceo_router", cast(Any, self.ceo_agent.execute))
        workflow.add_node("architect", cast(Any, self.architect_agent.execute))
        workflow.add_node("coder", cast(Any, self.engineer_agent.execute))
        workflow.add_node("devops", cast(Any, self.devops_agent.execute))
        workflow.add_node("qa", cast(Any, self.qa_agent.execute))
        workflow.add_node("task_decomposer", cast(Any, self.decomposer_agent.execute))

        # 2. Set Entry Point
        workflow.set_entry_point("ceo_router")

        # 3. Add Edges from CEO router
        workflow.add_conditional_edges(
            "ceo_router",
            self.ceo_agent.route_next_phase,
            {
                "architect": "architect",
                "coder": "coder",
                "devops": "devops",
                "qa": "qa",
                END: END,
            },
        )

        # 4. Linear development pipeline
        workflow.add_edge("architect", "coder")
        workflow.add_edge("coder", "devops")
        workflow.add_edge("devops", "qa")

        # 5. Conditional edge after QA: Self-correction vs Task decomposition vs Completion
        workflow.add_conditional_edges(
            "qa",
            lambda s: self.qa_agent.evaluate_verdict(s, max_retries=self.max_retries),
            {
                "coder": "coder",
                "task_decomposer": "task_decomposer",
                END: END,
            },
        )

        # 6. Decomposer routes back to coder with refined spec
        workflow.add_edge("task_decomposer", "coder")

        return workflow

    def compile(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        """Compile and cache the executable StateGraph."""
        if self._compiled_graph is None:
            workflow = self.assemble_workflow()
            self._compiled_graph = workflow.compile(checkpointer=self.checkpointer)
        return self._compiled_graph

    def assemble_master_workflow(
        self,
        provider: Any | None = None,
        tool_registry: Any | None = None,
    ) -> StateGraph[Any, Any, Any, Any]:
        """Assemble the deterministic LangGraph master graph with isolated subgraphs."""
        return create_orchestrator_workflow(
            project_root_resolver=self.project_root_resolver,
            tool_registry=tool_registry,
            provider=provider,
            simulate_initial_bug=self.simulate_initial_bug,
            max_retries=self.max_retries,
        )

    def compile_master_workflow(
        self,
        provider: Any | None = None,
        tool_registry: Any | None = None,
    ) -> CompiledStateGraph[Any, Any, Any, Any]:
        """Compile the deterministic LangGraph master graph with subgraphs ready for execution."""
        return compile_orchestrator_workflow(
            checkpointer=self.checkpointer,
            project_root_resolver=self.project_root_resolver,
            tool_registry=tool_registry,
            provider=provider,
            simulate_initial_bug=self.simulate_initial_bug,
            max_retries=self.max_retries,
        )

    def run_master_workflow(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        provider: Any | None = None,
        tool_registry: Any | None = None,
        resume_if_possible: bool = True,
        reset_state: bool = False,
    ) -> CompanyState:
        """Execute the master deterministic orchestrator workflow with subgraphs."""
        initial_state = self.prepare_initial_state(
            user_requirement=user_requirement,
            project_id=project_id,
            resume_if_possible=resume_if_possible,
            reset_state=reset_state,
        )
        compiled_master = self.compile_master_workflow(
            provider=provider,
            tool_registry=tool_registry,
        )
        config: RunnableConfig = {"configurable": {"thread_id": project_id}}
        final_state: CompanyState = cast(
            CompanyState, compiled_master.invoke(initial_state, config=config)
        )
        return final_state

    def prepare_initial_state(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        resume_if_possible: bool = True,
        reset_state: bool = False,
    ) -> CompanyState:
        """Resolve initial state from checkpoint or instantiate a clean new run."""
        if reset_state and self.checkpoint_path.is_file():
            self.checkpoint_path.unlink()

        persisted = load_company_state(self.checkpoint_path) if resume_if_possible else None

        if persisted and can_resume(persisted):
            return hydrate_resumed_state(persisted)

        return create_initial_state(user_requirement=user_requirement, project_id=project_id)

    def run(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        resume_if_possible: bool = True,
        reset_state: bool = False,
    ) -> CompanyState:
        """Synchronously execute the startup workflow to completion."""
        initial_state = self.prepare_initial_state(
            user_requirement=user_requirement,
            project_id=project_id,
            resume_if_possible=resume_if_possible,
            reset_state=reset_state,
        )

        compiled_graph = self.compile()
        config: RunnableConfig = {"configurable": {"thread_id": project_id}}

        final_state: CompanyState = cast(
            CompanyState, compiled_graph.invoke(initial_state, config=config)
        )
        return final_state

    async def astream_nodes(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        resume_if_possible: bool = True,
        reset_state: bool = False,
        use_master_graph: bool = True,
        tool_registry: Any | None = None,
    ) -> AsyncIterator[tuple[str, CompanyState]]:
        """Asynchronously stream node execution events and state snapshots.

        Yields tuples of ``(node_name, state_snapshot)`` as each node completes.
        """
        initial_state = self.prepare_initial_state(
            user_requirement=user_requirement,
            project_id=project_id,
            resume_if_possible=resume_if_possible,
            reset_state=reset_state,
        )

        compiled_graph = (
            self.compile_master_workflow(tool_registry=tool_registry)
            if use_master_graph
            else self.compile()
        )
        config: RunnableConfig = {"configurable": {"thread_id": project_id}}

        loop = asyncio.get_running_loop()

        # Run node generator in executor to bridge synchronous LangGraph runner
        def _get_stream() -> Any:
            return compiled_graph.stream(initial_state, config=config, stream_mode="updates")

        stream = await loop.run_in_executor(None, _get_stream)

        for chunk in stream:
            if isinstance(chunk, dict):
                for node_name, state_update in chunk.items():
                    yield str(node_name), cast(CompanyState, state_update)
            await asyncio.sleep(0)

    def triage_agent_capabilities(
        self, agent_name: str, task_type: str = "DEFAULT"
    ) -> dict[str, Any]:
        """Triage tool capabilities for an agent persona.

        - If agent has 0 tools or task_type == 'DIRECT_QA': bypass tool injection (pure text mode).
        - If agent has tools and dynamic mode is enabled:
          inject minimal baseline (search_available_tools).
        """
        agent_cfg = self.config.get_agent(agent_name)
        if task_type == "DIRECT_QA" or agent_cfg.is_tool_less:
            return {
                "mode": "text_completion",
                "tools_injected": 0,
                "tool_names": (),
                "dynamic_retrieval_active": False,
            }

        policy = agent_cfg.tool_policy
        return {
            "mode": "tool_calling",
            "tools_injected": len(policy.base_tools),
            "tool_names": policy.base_tools,
            "dynamic_retrieval_active": policy.enabled,
            "max_active_tools": policy.max_active_tools,
        }
