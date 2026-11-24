"""Shared builders for tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.domain.models import (
    AgentProviderConfig,
    ExecutionParams,
    ExecutionTask,
    Prompt,
)
from app.domain.ports import (
    IMermaidWorkflowVisualizer,
    INodeEventMapper,
    IOrchestratorEngine,
    IOrchestratorEngineFactory,
)
from app.domain.ports.agent_provider import ProviderDeps
from app.infrastructure.config import EnvSecretResolver
from app.infrastructure.orchestration import (
    MermaidWorkflowVisualizer,
    OrchestratorEngineFactory,
    StartupNodeEventMapper,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_CONFIG = REPO_ROOT / "config"


def make_prompt(user: str = "Say hello to the world", **kwargs: Any) -> Prompt:
    return Prompt(user_prompt=user, **kwargs)


def make_task(
    provider_id: str = "fake", user: str = "Say hello to the world", **param_kwargs: Any
) -> ExecutionTask:
    return ExecutionTask.create(
        make_prompt(user), ExecutionParams(provider_id=provider_id, **param_kwargs)
    )


def running_task(provider_id: str = "fake", **kwargs: Any) -> ExecutionTask:
    """A task already in RUNNING with attempt 1 (what providers actually receive)."""
    return make_task(provider_id, **kwargs).start(provider_id)


def make_config(
    id_: str = "fake",
    kind: str = "fake",
    *,
    options: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> AgentProviderConfig:
    return AgentProviderConfig(id=id_, kind=kind, options=dict(options or {}), **kwargs)


def make_deps(
    env: Mapping[str, str] | None = None,
    *,
    orchestrator_engine: IOrchestratorEngine | None = None,
    orchestrator_engine_factory: IOrchestratorEngineFactory | None = None,
    mermaid_visualizer: IMermaidWorkflowVisualizer | None = None,
    node_event_mapper_factory: Callable[[], INodeEventMapper] | None = None,
) -> ProviderDeps:
    factory = (
        orchestrator_engine_factory
        if orchestrator_engine_factory is not None
        else OrchestratorEngineFactory()
    )
    engine = (
        orchestrator_engine
        if orchestrator_engine is not None
        else factory.create(
            workspace_dir=REPO_ROOT / "projects",
            checkpoint_path=REPO_ROOT / "logs" / "company_state.json",
        )
    )
    return ProviderDeps(
        secrets=EnvSecretResolver(env or {}),
        orchestrator_engine=engine,
        orchestrator_engine_factory=factory,
        mermaid_visualizer=(
            mermaid_visualizer if mermaid_visualizer is not None else MermaidWorkflowVisualizer()
        ),
        node_event_mapper_factory=(
            node_event_mapper_factory
            if node_event_mapper_factory is not None
            else StartupNodeEventMapper
        ),
    )
