"""DEPRECATED: Compatibility shim delegating ``GraphTurnOrchestrator`` to OOP facade.

Prefer ``app.infrastructure.orchestration.TurnOrchestratorService`` and
``MermaidWorkflowVisualizer``.
"""

from __future__ import annotations

__deprecated__ = True

from pathlib import Path

from app.application.tools.registry import DynamicToolRegistry
from app.application.triage import CascadingTriageService, get_triage_service
from app.infrastructure.orchestration import (
    MermaidWorkflowVisualizer,
    OrchestratorConfigLoader,
    OrchestratorEngineFactory,
    StartupNodeEventMapper,
    TurnOrchestratorService,
)

from company_core.config import ConfigManager, get_config
from company_core.skills import SkillRegistry, get_default_skill_registry


def is_direct_query(
    prompt: str,
    triage_service: CascadingTriageService | None = None,
) -> bool:
    """Check if the user prompt resolves to ``DIRECT_QA`` via cascading triage."""
    svc = triage_service or get_triage_service()
    return svc.triage(prompt.strip()).intent == "DIRECT_QA"


def generate_mermaid_workflow(
    user_requirement: str,
    status: str = "WAITING_APPROVAL",
    provider_name: str = "Agent",
    intent: str | None = None,
    triage_service: CascadingTriageService | None = None,
) -> str:
    """Generate deterministic Mermaid diagram tailored to resolved intent."""
    visualizer = MermaidWorkflowVisualizer(profile_classifier=get_config())
    return visualizer.generate(
        user_requirement,
        status=status,
        provider_name=provider_name,
        intent=intent,
        triage_service=triage_service,
    )


class GraphTurnOrchestrator(TurnOrchestratorService):
    """Backward-compatible subclass delegating to ``TurnOrchestratorService``."""

    def __init__(
        self,
        checkpoint_dir: Path | None = None,
        max_retries: int = 3,
        tool_registry: DynamicToolRegistry | None = None,
        config: ConfigManager | None = None,
        skill_registry: SkillRegistry | None = None,
        triage_service: CascadingTriageService | None = None,
    ) -> None:
        cfg = config or get_config()
        skills = skill_registry or get_default_skill_registry()
        triage_svc = triage_service or get_triage_service()
        super().__init__(
            checkpoint_dir=checkpoint_dir,
            max_retries=max_retries,
            tool_registry=tool_registry,
            profile_classifier=cfg,
            skill_registry=skills,
            triage_service=triage_svc,
            mermaid_visualizer=MermaidWorkflowVisualizer(profile_classifier=cfg),
            engine_factory=OrchestratorEngineFactory(
                profile_classifier=cfg,
                skill_registry=skills,
                tool_registry=tool_registry,
                triage_service=triage_svc,
            ),
        )


__all__ = [
    "GraphTurnOrchestrator",
    "MermaidWorkflowVisualizer",
    "OrchestratorConfigLoader",
    "StartupNodeEventMapper",
    "TurnOrchestratorService",
    "generate_mermaid_workflow",
    "is_direct_query",
]
