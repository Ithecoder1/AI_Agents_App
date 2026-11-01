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
    assert caps.can_modify_workspace is True
    assert "langgraph-startup-orchestrator" in caps.models


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
