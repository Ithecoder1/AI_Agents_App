"""Backward-compatibility shim bridging CascadingTriageService with Orchestrator profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.application.triage import CascadingTriageService, TriageDecision, get_triage_service
from app.domain.graph_state import INTENT_COMPLEX_PIPELINE
from app.infrastructure.orchestration.state.triage_bridge import TriageProfileResolver

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
    """Thin backward-compatible delegate to ``TriageProfileResolver.classify_and_resolve``."""
    return TriageProfileResolver(
        profiles=profiles,
        default_intent=default_intent,
        triage_service=triage_service or get_triage_service(),
    ).classify_and_resolve(requirement, is_lightweight=is_lightweight)


__all__ = ["TriageProfileResolver", "resolve_profile_with_triage"]
