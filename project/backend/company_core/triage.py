"""Backward-compatibility shim bridging CascadingTriageService with Orchestrator profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.application.triage import CascadingTriageService, TriageDecision, get_triage_service
from app.domain.graph_state import INTENT_COMPLEX_PIPELINE
from app.infrastructure.orchestration.state.triage_bridge import (
    resolve_profile_with_triage as _resolve_profile_with_triage,
)

if TYPE_CHECKING:
    from company_core.config import OrchestratorProfileConfig


def resolve_profile_with_triage(
    requirement: str,
    profiles: Mapping[str, OrchestratorProfileConfig],
    *,
    default_intent: str = INTENT_COMPLEX_PIPELINE,
    triage_service: CascadingTriageService | None = None,
    is_lightweight: bool = False,
) -> tuple[OrchestratorProfileConfig, TriageDecision]:
    """Classify *requirement* via ``CascadingTriageService`` and map to an execution profile."""
    service = triage_service or get_triage_service()
    return _resolve_profile_with_triage(
        requirement,
        profiles,
        default_intent=default_intent,
        triage_service=service,
        is_lightweight=is_lightweight,
    )


__all__ = ["resolve_profile_with_triage"]
