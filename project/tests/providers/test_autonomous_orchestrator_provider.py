"""Tests for StartupOrchestratorProvider adapter."""

from __future__ import annotations

from pathlib import Path

from app.domain.enums import EventType
from app.domain.models import ExecutionParams, ExecutionTask, Prompt
from providers.adapters.autonomous_orchestrator import StartupOrchestratorProvider

from tests.factories import make_config, make_deps


def _create_orchestrator_provider(**options: object) -> StartupOrchestratorProvider:
    cfg = make_config(
        id_="autonomous-orchestrator",
        kind="autonomous_orchestrator",
        adapter="providers.adapters.autonomous_orchestrator:StartupOrchestratorProvider",
        options=options,
    )
    return StartupOrchestratorProvider(cfg, make_deps())


async def test_orchestrator_provider_health_check() -> None:
    provider = _create_orchestrator_provider()
    health = await provider.health_check()
    assert health.ok is True


async def test_orchestrator_provider_capabilities() -> None:
    provider = _create_orchestrator_provider()
    caps = provider.capabilities
    assert caps.streaming is True
    assert caps.supports_resume is True
    assert caps.supports_approval is True
    assert caps.can_modify_workspace is True
    assert "langgraph-startup-orchestrator" in caps.models


async def test_orchestrator_provider_approval_pause_and_resume(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "approval_ws"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    provider = _create_orchestrator_provider()

    prompt = Prompt(
        user_prompt="Build a full authentication service with pytest suite",
        literal=True,
    )
    params = ExecutionParams(
        provider_id="autonomous-orchestrator",
        extra={"require_approval": True},
    )
    task = ExecutionTask.create(prompt, params, workspace=str(workspace_dir)).start(
        "autonomous-orchestrator"
    )

    events = []
    stream_gen = provider.stream_execute(task)

    # 1. First event: INITIALIZING
    ev1 = await stream_gen.__anext__()
    events.append(ev1)
    assert ev1.type is EventType.STATUS
    assert ev1.data["phase"] == "INITIALIZING"

    # 2. Plan event
    ev2 = await stream_gen.__anext__()
    events.append(ev2)
    assert ev2.type is EventType.PLAN

    # 3. Waiting approval status event with Mermaid code
    ev3 = await stream_gen.__anext__()
    events.append(ev3)
    assert ev3.type is EventType.STATUS
    assert ev3.data["state"] == "waiting_approval"
    assert "mermaid_code" in ev3.data
    assert "flowchart" in ev3.data["mermaid_code"]

    # 4. Trigger approval
    approved = await provider.approve(task.id)
    assert approved is True

    # 5. Next event should be APPROVED running event
    ev4 = await stream_gen.__anext__()
    events.append(ev4)
    assert ev4.type is EventType.STATUS
    assert ev4.data["phase"] == "APPROVED"
    assert ev4.data["state"] == "running"

    # Complete stream
    async for ev in stream_gen:
        events.append(ev)

    done_events = [e for e in events if e.type is EventType.DONE]
    assert len(done_events) == 1
    assert done_events[0].data["finish_reason"] == "stop"


async def test_orchestrator_provider_approval_cancellation(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "cancel_ws"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    provider = _create_orchestrator_provider()

    prompt = Prompt(
        user_prompt="Build a full microservice with tests",
        literal=True,
    )
    params = ExecutionParams(
        provider_id="autonomous-orchestrator",
        extra={"require_approval": True},
    )
    task = ExecutionTask.create(prompt, params, workspace=str(workspace_dir)).start(
        "autonomous-orchestrator"
    )

    stream_gen = provider.stream_execute(task)

    # Advance until waiting approval
    await stream_gen.__anext__()
    await stream_gen.__anext__()
    ev3 = await stream_gen.__anext__()
    assert ev3.data["state"] == "waiting_approval"

    # Cancel task
    await provider.cancel(task.id)

    # Next events should be cancelled status and done
    cancel_ev = await stream_gen.__anext__()
    assert cancel_ev.type is EventType.STATUS
    assert cancel_ev.data["state"] == "cancelled"

    done_ev = await stream_gen.__anext__()
    assert done_ev.type is EventType.DONE
    assert done_ev.data["finish_reason"] == "cancelled"


async def test_orchestrator_provider_stream_execute(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "stream_ws"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    provider = _create_orchestrator_provider()

    prompt = Prompt(
        user_prompt="solve: 1+1 arithmetic evaluator",
        literal=True,
    )
    params = ExecutionParams(provider_id="autonomous-orchestrator")
    task = ExecutionTask.create(prompt, params, workspace=str(workspace_dir)).start(
        "autonomous-orchestrator"
    )

    events = []
    async for event in provider.stream_execute(task):
        events.append(event)

    types = [e.type for e in events]
    assert EventType.STATUS in types
    assert EventType.PLAN in types
    assert EventType.THOUGHT in types
    assert EventType.TOKEN in types
    assert EventType.ARTIFACT in types
    assert EventType.DONE in types

    # Check that code files were written to workspace disk
    assert (workspace_dir / "app" / "engine.py").is_file()
    assert (workspace_dir / "tests" / "test_engine.py").is_file()
    assert (workspace_dir / "company_state.json").is_file()
