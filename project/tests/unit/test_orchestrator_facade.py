"""OOP unit and contract tests for orchestration facade, Mermaid visualizer, and DI callers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from app.application.tools.registry import DynamicToolRegistry
from app.application.triage import CallableMicroLLMClient, CascadingTriageService
from app.domain.enums import EventType, TaskStatus
from app.domain.errors import ProviderError
from app.domain.graph_state import (
    INTENT_ARCHITECTURE_DESIGN,
    INTENT_CODE_TEST_FIX,
    INTENT_COMPLEX_PIPELINE,
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
    PHASE_ARCHITECT,
    PHASE_CODER,
    STATUS_COMPLETED,
    GraphState,
    create_initial_graph_state,
)
from app.domain.models import (
    AgentProviderConfig,
    ExecutionParams,
    ExecutionResult,
    ExecutionTask,
    HealthStatus,
    Prompt,
    ProviderCapabilities,
    StreamEvent,
)
from app.domain.ports import (
    IMermaidWorkflowVisualizer,
    INodeEventMapper,
    IOrchestratorEngine,
    IOrchestratorEngineFactory,
    ITurnOrchestrator,
    ProviderDeps,
)
from app.domain.tools import ToolDescriptor
from app.infrastructure.config import EnvSecretResolver
from app.infrastructure.orchestration import (
    GraphStateHydrator,
    LocalCodeWorkspaceRunner,
    MermaidWorkflowVisualizer,
    OrchestratorDependencies,
    OrchestratorEngine,
    OrchestratorEngineFactory,
    StartupNodeEventMapper,
    TurnOrchestratorService,
    compile_orchestrator_graph,
)
from providers.adapters.autonomous_orchestrator import StartupOrchestratorProvider


class _StubProvider:
    def __init__(self, *, text: str = "Fast-path answer") -> None:
        self.config = AgentProviderConfig(id="stub", kind="mock", display_name="StubAI")
        self._text = text
        self.recorded_tools: list[tuple[str, ...]] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True)

    async def execute(
        self,
        task: ExecutionTask,
        *,
        active_tools: Any = None,
        retrieval_policy: Any = None,
    ) -> ExecutionResult:
        _ = retrieval_policy
        self.recorded_tools.append(tuple(t.name for t in (active_tools or ())))
        return ExecutionResult(
            task_id=task.id,
            status=TaskStatus.SUCCEEDED,
            provider_id=self.config.id,
            output_text=self._text,
        )

    async def stream_execute(
        self,
        task: ExecutionTask,
        *,
        active_tools: Any = None,
        retrieval_policy: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        _ = retrieval_policy
        self.recorded_tools.append(tuple(t.name for t in (active_tools or ())))
        yield StreamEvent.create(task.id, task.attempt or 1, EventType.TOKEN, text=self._text)
        yield StreamEvent.create(task.id, task.attempt or 1, EventType.DONE, finish_reason="stop")

    async def health_check(self) -> HealthStatus:
        return HealthStatus.up()

    async def cancel(self, task_id: str) -> None:
        _ = task_id

    async def approve(self, task_id: str) -> bool:
        _ = task_id
        return True

    async def aclose(self) -> None:
        return None


def _triage_stub() -> CascadingTriageService:
    return CascadingTriageService(
        micro_llm_client=CallableMicroLLMClient(
            lambda _s, _u: (
                '{"intent": "COMPLEX_PIPELINE", "confidence": 0.9, '
                '"requires_workspace_manifest": true, '
                '"target_model_tier": "reasoning", "token_budget": 4096}'
            )
        )
    )


def test_mermaid_visualizer_and_event_mapper_contracts() -> None:
    visualizer = MermaidWorkflowVisualizer()
    assert isinstance(visualizer, IMermaidWorkflowVisualizer)

    assert "Fast-Path Executor" in visualizer.generate(
        "/help",
        intent=INTENT_DIRECT_QA,
        status="WAITING_APPROVAL",
    )
    assert "Fast-Path: 0 Tools" in visualizer.generate(
        "/help",
        intent=INTENT_DIRECT_QA,
        status="RUNNING",
    )
    assert "Focused Tool Executor" in visualizer.generate(
        "inspect app",
        intent=INTENT_FOCUSED_TOOL_TASK,
    )
    assert "ArchitectSub" in visualizer.generate(
        "design system",
        intent=INTENT_ARCHITECTURE_DESIGN,
    )
    coder_diag = visualizer.generate("fix bug", intent=INTENT_CODE_TEST_FIX)
    assert "CoderSub" in coder_diag and "ArchitectSub" not in coder_diag
    full_diag = visualizer.generate("build api", intent=INTENT_COMPLEX_PIPELINE)
    assert "ArchitectSub" in full_diag and "CoderSub" in full_diag

    mapper = StartupNodeEventMapper()
    assert isinstance(mapper, INodeEventMapper)
    assert len(mapper.plan_steps) == 5

    state = create_initial_graph_state("Build 1+1", project_id="map-1")
    state["intent"] = INTENT_COMPLEX_PIPELINE
    state["active_skills"] = ["architecture_planning"]
    state["architecture_spec"] = "# Spec"
    state["code_files"] = {"app/engine.py": "x = 1\n"}
    state["test_results"] = "PASSED: ok"

    assert any(e.type is EventType.STATUS for e in mapper.map_node("t1", 1, "triage_node", state))
    assert any(
        e.type is EventType.ARTIFACT for e in mapper.map_node("t1", 1, "architect_subgraph", state)
    )
    assert any(
        e.type is EventType.ARTIFACT for e in mapper.map_node("t1", 1, "coder_qa_subgraph", state)
    )
    qa_events = mapper.map_node("t1", 1, "evaluator_node", state)
    assert any(e.type is EventType.LOG for e in qa_events)
    assert all(step["status"] == "done" for step in mapper.plan_steps)


@pytest.mark.asyncio
async def test_orchestrator_engine_invoke_astream_and_checkpoint_resume(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "logs" / "state.json"
    runner = LocalCodeWorkspaceRunner(base_workspace_dir=tmp_path / "ws")
    hydrator = GraphStateHydrator(default_workspace_dir=checkpoint_path.parent)

    engine = OrchestratorEngine(
        dependencies=OrchestratorDependencies(
            hydrator=hydrator,
            workspace_runner=runner,
            triage_service=_triage_stub(),
        ),
        checkpoint_path=checkpoint_path,
    )
    assert isinstance(engine, IOrchestratorEngine)
    assert engine.checkpoint_path == checkpoint_path

    interrupted = create_initial_graph_state("Build calculator", project_id="res-p")
    interrupted["architecture_spec"] = "# Preloaded Blueprint\n## Component Boundaries"
    interrupted["completed_tasks"] = ["architecture"]
    interrupted["current_phase"] = PHASE_ARCHITECT
    hydrator.save(interrupted, checkpoint_path)

    resumed_state = engine.prepare_initial_state("Build calculator", project_id="res-p")
    assert resumed_state["current_phase"] == PHASE_CODER

    fresh_state = engine.prepare_initial_state(
        "Build calculator",
        project_id="res-p",
        reset_state=True,
    )
    assert fresh_state["current_phase"] == PHASE_ARCHITECT
    assert not checkpoint_path.exists()

    streamed: list[tuple[str, GraphState]] = []
    async for node_name, snapshot in engine.astream(
        "/help",
        project_id="stream-direct",
        reset_state=True,
    ):
        streamed.append((node_name, snapshot))
    assert any(name == "executor_node" for name, _ in streamed)

    factory = OrchestratorEngineFactory(triage_service=_triage_stub())
    assert isinstance(factory, IOrchestratorEngineFactory)
    pinned_engine = factory.create(
        workspace_dir=tmp_path / "pinned",
        checkpoint_path=tmp_path / "pinned" / "company_state.json",
    )
    final = pinned_engine.run_master_workflow("solve: 1+1 arithmetic evaluator", project_id="pin")
    assert final["status"] == STATUS_COMPLETED
    assert (tmp_path / "pinned" / "app" / "engine.py").is_file()
    assert compile_orchestrator_graph(triage_service=_triage_stub()) is not None


@pytest.mark.asyncio
async def test_turn_orchestrator_service_fast_paths_and_approval_cancel(tmp_path: Path) -> None:
    tool_reg = DynamicToolRegistry(
        [
            ToolDescriptor("view_file", "filesystem", "View source file contents"),
            ToolDescriptor("list_dir", "filesystem", "List workspace files"),
        ]
    )
    service = TurnOrchestratorService(
        checkpoint_dir=tmp_path / "turns",
        tool_registry=tool_reg,
        triage_service=_triage_stub(),
    )
    assert isinstance(service, ITurnOrchestrator)

    provider = _StubProvider(text="2")
    direct_task = ExecutionTask.create(
        Prompt(user_prompt="/help", literal=True),
        ExecutionParams(provider_id="stub"),
    ).start("stub")
    direct_events = [ev async for ev in service.stream_turn(direct_task, cast("Any", provider))]
    assert provider.recorded_tools[-1] == ()
    assert any(ev.type is EventType.DONE for ev in direct_events)

    focused_task = ExecutionTask.create(
        Prompt(user_prompt="inspect workspace files", literal=True),
        ExecutionParams(provider_id="stub"),
    ).start("stub")
    focused_events = [ev async for ev in service.stream_turn(focused_task, cast("Any", provider))]
    assert len(provider.recorded_tools[-1]) >= 1
    assert any(ev.type is EventType.PLAN for ev in focused_events)

    cancel_evt = asyncio.Event()
    approval_evt = asyncio.Event()
    gated_task = ExecutionTask.create(
        Prompt(user_prompt="Build microservice with pytest suite", literal=True),
        ExecutionParams(provider_id="stub"),
    ).start("stub")

    stream = service.stream_turn(
        gated_task,
        cast("Any", provider),
        require_approval=True,
        approval_event=approval_evt,
        cancel_event=cancel_evt,
    )
    seen_waiting = False
    async for ev in stream:
        if ev.type is EventType.STATUS and ev.data.get("state") == "waiting_approval":
            seen_waiting = True
            cancel_evt.set()
        last_ev = ev
    assert seen_waiting is True
    assert last_ev.type is EventType.DONE
    assert last_ev.data["finish_reason"] == "cancelled"


@pytest.mark.asyncio
async def test_startup_orchestrator_provider_requires_injected_engine(tmp_path: Path) -> None:
    cfg = AgentProviderConfig(
        id="autonomous-orchestrator",
        kind="autonomous_orchestrator",
        adapter="providers.adapters.autonomous_orchestrator:StartupOrchestratorProvider",
    )
    minimal_deps = ProviderDeps(secrets=EnvSecretResolver({}))
    provider = StartupOrchestratorProvider(cfg, minimal_deps)
    task = ExecutionTask.create(
        Prompt(user_prompt="Build 1+1", literal=True),
        ExecutionParams(provider_id="autonomous-orchestrator"),
        workspace=str(tmp_path),
    ).start("autonomous-orchestrator")

    with pytest.raises(ProviderError, match="orchestrator_engine"):
        async for _ in provider.stream_execute(task):
            pass
