"""OOP unit and contract tests for isolated Master Graph nodes, routing, and MasterGraphBuilder."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from app.application.tools.registry import DynamicToolRegistry
from app.application.triage import CallableMicroLLMClient, CascadingTriageService
from app.domain.enums import EventType, TaskStatus
from app.domain.graph_state import (
    INTENT_ARCHITECT_PIPELINE,
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
    PHASE_CODER,
    PHASE_COMPLETED,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    GraphState,
    create_initial_graph_state,
)
from app.domain.models import (
    AgentProviderConfig,
    ExecutionResult,
    ExecutionTask,
    HealthStatus,
    StreamEvent,
)
from app.domain.ports.agent_provider import ProviderCapabilities
from app.domain.tools import ToolDescriptor
from app.infrastructure.orchestration import (
    BaseGraphNode,
    ExecutionLogManager,
    ExecutorNode,
    JsonStateCheckpointer,
    LocalCodeWorkspaceRunner,
    MasterGraphBuilder,
    MasterGraphRouter,
    OrchestratorConfigLoader,
    PlannerNode,
    RouterNode,
    SkillRegistry,
    TaskDecomposerNode,
    TaskDecompositionStrategy,
    TriageNode,
    TriageProfileResolver,
    build_master_graph,
    compile_master_graph,
    route_after_architect,
    route_after_coder_qa,
    route_after_planner,
    route_after_router,
    route_after_triage,
)
from company_core import orchestrator_graph as legacy_graph
from langgraph.graph import END


class _MockProvider:
    def __init__(self, *, response_text: str = "Mock OK", should_fail: bool = False) -> None:
        self.config = AgentProviderConfig(
            id="mock-provider",
            kind="mock",
            display_name="MockProvider",
        )
        self._response_text = response_text
        self._should_fail = should_fail
        self.calls: list[str] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def is_available(self) -> bool:
        return True

    async def execute(
        self,
        task: ExecutionTask,
        *,
        active_tools: Any = None,
        retrieval_policy: Any = None,
    ) -> ExecutionResult:
        _ = (active_tools, retrieval_policy)
        self.calls.append(task.prompt_snapshot.user_prompt)
        if self._should_fail:
            raise RuntimeError("mock upstream timeout")
        return ExecutionResult(
            task_id=task.id,
            status=TaskStatus.SUCCEEDED,
            provider_id=self.config.id,
            output_text=self._response_text,
        )

    async def stream_execute(
        self,
        task: ExecutionTask,
        *,
        active_tools: Any = None,
        retrieval_policy: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        res = await self.execute(
            task,
            active_tools=active_tools,
            retrieval_policy=retrieval_policy,
        )
        yield StreamEvent(task_id=task.id, seq=0, type=EventType.TOKEN, text=res.output_text)
        yield StreamEvent(task_id=task.id, seq=1, type=EventType.DONE)

    async def cancel(self, task_id: str) -> None:
        _ = task_id

    async def approve(self, task_id: str) -> bool:
        _ = task_id
        return True

    async def aclose(self) -> None:
        return None

    async def health_check(self) -> HealthStatus:
        return HealthStatus.up()


def _triage_service() -> CascadingTriageService:
    return CascadingTriageService(
        micro_llm_client=CallableMicroLLMClient(
            lambda _sys, _usr: (
                '{"intent": "COMPLEX_PIPELINE", "confidence": 0.9, '
                '"requires_workspace_manifest": true, '
                '"target_model_tier": "reasoning", "token_budget": 4096}'
            )
        )
    )


@pytest.mark.asyncio
async def test_triage_and_planner_and_router_command_nodes() -> None:
    classifier = OrchestratorConfigLoader.default()
    skills = SkillRegistry.default()
    logs = ExecutionLogManager()
    tool_registry = DynamicToolRegistry(
        [
            ToolDescriptor("view_file", "filesystem", "View source file contents"),
            ToolDescriptor("write_file", "filesystem", "Write source file contents"),
        ]
    )

    triage_node = TriageNode(
        profile_classifier=classifier,
        skill_registry=skills,
        log_manager=logs,
        tool_registry=tool_registry,
        triage_service=_triage_service(),
        profile_resolver=TriageProfileResolver(classifier),
    )
    assert isinstance(triage_node, BaseGraphNode)

    direct_state = create_initial_graph_state("/help", project_id="triage-direct")
    direct_out = cast("GraphState", await triage_node(direct_state))
    assert direct_out["intent"] == INTENT_DIRECT_QA
    assert direct_out["active_tools"] == []
    assert direct_out["current_node"] == "triage_node"

    focused_state = create_initial_graph_state(
        "inspect auth module in workspace",
        project_id="triage-focus",
    )
    focused_out = cast("GraphState", triage_node.execute(focused_state))
    assert focused_out["intent"] == INTENT_FOCUSED_TOOL_TASK
    assert len(focused_out["active_tools"]) >= 1

    planner = PlannerNode(profile_classifier=classifier, log_manager=logs)
    gated_state = create_initial_graph_state(
        "Build billing engine",
        project_id="plan-gate",
        auto_approve=False,
    )
    gated_state["intent"] = INTENT_ARCHITECT_PIPELINE
    planned_gated = cast("GraphState", await planner(gated_state))
    assert planned_gated["status"] == STATUS_WAITING_APPROVAL
    assert planned_gated["current_node"] == "planner_node"
    assert len(planned_gated["plan"]) >= 1

    override_state = create_initial_graph_state(
        "Analyze module",
        project_id="plan-override",
        auto_approve=False,
    )
    override_state["task_type"] = "DIRECT_QA"
    planned_override = cast("GraphState", planner.execute(override_state))
    assert planned_override["status"] == STATUS_RUNNING

    router_node = RouterNode(log_manager=logs)
    routed = cast("GraphState", await router_node(planned_override))
    assert routed["current_node"] == "subgraph_router_node"
    assert any("Router: Dispatching" in entry["message"] for entry in routed["execution_logs"])


@pytest.mark.asyncio
async def test_executor_and_task_decomposer_command_nodes() -> None:
    logs = ExecutionLogManager()

    mock_provider = _MockProvider(response_text="42")
    executor = ExecutorNode(log_manager=logs, provider=cast("Any", mock_provider))
    async_out = cast(
        "GraphState",
        await executor(create_initial_graph_state("What is 1+1?", project_id="exec-async")),
    )
    assert async_out["status"] == STATUS_COMPLETED
    assert async_out["current_phase"] == PHASE_COMPLETED
    assert async_out["subgraph_results"]["executor"]["output"] == "42"
    assert mock_provider.calls == ["What is 1+1?"]

    failing_executor = ExecutorNode(
        log_manager=logs,
        provider=cast("Any", _MockProvider(should_fail=True)),
    )
    fail_out = cast(
        "GraphState",
        await failing_executor(create_initial_graph_state("hello", project_id="exec-err")),
    )
    assert fail_out["status"] == STATUS_COMPLETED
    assert "Execution error via provider" in fail_out["subgraph_results"]["executor"]["output"]

    fallback_executor = ExecutorNode(log_manager=logs)
    sync_out = cast(
        "GraphState",
        fallback_executor.execute(create_initial_graph_state("Solve 1+1", project_id="exec-fb")),
    )
    assert sync_out["subgraph_results"]["executor"]["output"] == "The result of 1 + 1 is 2."

    decomposer = TaskDecomposerNode(
        decomposition_strategy=TaskDecompositionStrategy(),
        log_manager=logs,
    )
    assert decomposer.name == "task_decomposer"
    failed_state = create_initial_graph_state("Build math", project_id="decomp")
    failed_state["architecture_spec"] = "# Initial Blueprint"
    failed_state["retry_count"] = 3
    failed_state["error_message"] = "AssertionError: 1001 != 2"
    decomposed = cast("GraphState", await decomposer(failed_state))
    assert decomposed["current_node"] == "task_decomposer_node"
    assert decomposed["current_phase"] == PHASE_CODER
    assert decomposed["retry_count"] == 0
    assert decomposed["error_message"] is None
    assert "# Initial Blueprint" in (decomposed["architecture_spec"] or "")
    assert "Task Decomposition Plan" in (decomposed["architecture_spec"] or "")


def test_master_graph_router_and_pure_routing_functions() -> None:
    router = MasterGraphRouter(default_max_retries=2)

    s_direct = create_initial_graph_state("/help")
    s_direct["intent"] = INTENT_DIRECT_QA
    assert router.route_after_triage(s_direct) == "executor_node"
    assert route_after_triage(s_direct) == "executor_node"

    s_direct["auto_approve"] = False
    assert route_after_triage(s_direct) == "planner_node"

    s_wait = create_initial_graph_state("Build app")
    s_wait["status"] = STATUS_WAITING_APPROVAL
    assert route_after_planner(s_wait) == str(END)
    assert route_after_router(s_wait) == str(END)

    s_focus = create_initial_graph_state("Inspect files")
    s_focus["intent"] = INTENT_FOCUSED_TOOL_TASK
    assert route_after_planner(s_focus) == "executor_node"

    s_full = create_initial_graph_state("Build full system")
    s_full["task_type"] = "FULL_PIPELINE"
    assert route_after_router(s_full) == "architect_subgraph"
    s_full["completed_tasks"] = ["architecture"]
    s_full["architecture_spec"] = "Spec"
    assert route_after_router(s_full) == "coder_qa_subgraph"
    s_full["completed_tasks"] = ["architecture", "qa"]
    assert route_after_router(s_full) == str(END)

    s_arch = create_initial_graph_state("Design architecture")
    s_arch["execution_profile"] = {"subgraphs": ["architect_subgraph"]}
    assert route_after_architect(s_arch) == str(END)
    s_arch["execution_profile"] = {"subgraphs": ["architect_subgraph", "coder_qa_subgraph"]}
    assert route_after_architect(s_arch) == "coder_qa_subgraph"

    s_retry = create_initial_graph_state("Test retry")
    s_retry["test_results"] = "FAILED"
    s_retry["retry_count"] = 2
    assert router.is_retry_exhausted(s_retry) is False  # GraphState default max_retries=3
    s_retry["max_retries"] = 2
    assert router.route_after_coder_qa(s_retry) == "task_decomposer"
    s_retry["test_results"] = "PASSED: ok"
    assert route_after_coder_qa(s_retry) == str(END)


@pytest.mark.asyncio
async def test_master_graph_builder_sync_async_and_legacy_shim(tmp_path: Path) -> None:
    checkpointer = JsonStateCheckpointer(log_path=tmp_path / "logs" / "master.json")
    runner = LocalCodeWorkspaceRunner(base_workspace_dir=tmp_path / "ws")

    builder = MasterGraphBuilder(
        checkpointer=checkpointer,
        workspace_runner=runner,
        triage_service=_triage_service(),
        provider=cast("Any", _MockProvider(response_text="async-ok")),
    )
    assert builder.checkpointer is checkpointer
    assert isinstance(builder.triage_node, TriageNode)
    assert isinstance(builder.executor_node, ExecutorNode)

    compiled = builder.compile()
    async_out = await compiled.ainvoke(
        create_initial_graph_state("/help", project_id="mg-async"),
        config={"configurable": {"thread_id": "mg-async"}},
    )
    assert async_out["status"] == STATUS_COMPLETED
    assert async_out["subgraph_results"]["executor"]["output"] == "async-ok"

    sync_graph = compile_master_graph(
        checkpointer=checkpointer,
        workspace_runner=runner,
        triage_service=_triage_service(),
    )
    sync_out = sync_graph.invoke(
        create_initial_graph_state("Build 1+1 evaluator", project_id="mg-sync"),
        config={"configurable": {"thread_id": "mg-sync"}},
    )
    assert sync_out["status"] == STATUS_COMPLETED
    assert "architecture" in sync_out["completed_tasks"]
    assert "qa" in sync_out["completed_tasks"]
    assert build_master_graph(triage_service=_triage_service()) is not None

    legacy_compiled = legacy_graph.compile_orchestrator_workflow(
        checkpointer=JsonStateCheckpointer(log_path=tmp_path / "logs" / "legacy.json"),
        project_root_resolver=lambda pid: tmp_path / "legacy" / pid,
        triage_service=_triage_service(),
    )
    legacy_out = legacy_compiled.invoke(
        create_initial_graph_state("/help", project_id="mg-legacy"),
        config={"configurable": {"thread_id": "mg-legacy"}},
    )
    assert legacy_out["status"] == STATUS_COMPLETED
