"""Bridge adapting CascadingTriageService decisions to orchestrator execution profiles."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.application.triage import (
    DEFAULT_RESOURCE_ALLOCATION,
    CascadingTriageService,
    TriageDecision,
    get_triage_service,
)
from app.domain.graph_state import (
    INTENT_ARCHITECTURE_DESIGN,
    INTENT_CODE_TEST_FIX,
    INTENT_COMPLEX_PIPELINE,
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
)

if TYPE_CHECKING:
    from app.infrastructure.orchestration.state.profiles import OrchestratorProfileConfig

_FOCUSED_INSPECT_RE = re.compile(
    r"\b(analyze|inspect|audit|search|lint|find in files|check file|review code)\b",
    re.IGNORECASE,
)
_MODIFY_OR_TEST_RE = re.compile(
    r"\b(fix|bug|refactor|clean up|cleanup|optimize|patch|pytest|test|tests|unit test|"
    r"build|create|scaffold|implement|develop|migration|endpoint)\b",
    re.IGNORECASE,
)
_ARCH_ONLY_RE = re.compile(
    r"\b(architect|blueprint|design spec|system design|schema design|architecture diagram)\b",
    re.IGNORECASE,
)
_BUILD_OR_SCAFFOLD_RE = re.compile(
    r"\b(build|create|scaffold|implement|develop|migration|endpoint|service layer|"
    r"from scratch|solve|calculator|evaluator)\b",
    re.IGNORECASE,
)
_LEADING_BUILD_PIPELINE_RE = re.compile(
    r"^\s*(build|create|develop|implement|scaffold)\b",
    re.IGNORECASE,
)


def _lookup_profile(
    profiles: Mapping[str, OrchestratorProfileConfig],
    intent_key: str,
    default_intent: str,
) -> OrchestratorProfileConfig:
    if intent_key in profiles:
        return profiles[intent_key]
    if default_intent in profiles:
        return profiles[default_intent]
    return next(iter(profiles.values()))


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
    cleaned = requirement.strip()

    if is_lightweight:
        decision = service.triage(cleaned)
        if decision.intent != "DIRECT_QA":
            alloc = DEFAULT_RESOURCE_ALLOCATION["DIRECT_QA"]
            decision = TriageDecision(
                intent="DIRECT_QA",
                confidence=1.0,
                requires_workspace_manifest=alloc.requires_workspace_manifest,
                target_model_tier=alloc.target_model_tier,
                token_budget=alloc.token_budget,
                tier=1,
                latency_ms=decision.latency_ms,
                matched_route="tier1_lightweight_override",
                reason="Explicit lightweight fast-path mapped to DIRECT_QA",
            )
        return _lookup_profile(profiles, INTENT_DIRECT_QA, default_intent), decision

    decision = service.triage(cleaned)

    # Read-only analysis/inspection queries map to FOCUSED_TOOL_TASK when no edit/test verbs exist
    if (
        decision.intent in ("DIRECT_QA", "CODE_TEST_FIX")
        and _FOCUSED_INSPECT_RE.search(cleaned)
        and not _MODIFY_OR_TEST_RE.search(cleaned)
        and INTENT_FOCUSED_TOOL_TASK in profiles
    ):
        return _lookup_profile(profiles, INTENT_FOCUSED_TOOL_TASK, default_intent), decision

    if decision.intent == "DIRECT_QA":
        return _lookup_profile(profiles, INTENT_DIRECT_QA, default_intent), decision

    if _LEADING_BUILD_PIPELINE_RE.search(cleaned) and INTENT_COMPLEX_PIPELINE in profiles:
        return _lookup_profile(profiles, INTENT_COMPLEX_PIPELINE, default_intent), decision

    if decision.intent == "CODE_TEST_FIX":
        return _lookup_profile(profiles, INTENT_CODE_TEST_FIX, default_intent), decision

    # ARCHITECT_PIPELINE -> specialized ARCHITECTURE_DESIGN vs full COMPLEX_PIPELINE
    if (
        INTENT_ARCHITECTURE_DESIGN in profiles
        and (_ARCH_ONLY_RE.search(cleaned) or re.match(r"^\s*design\b", cleaned, re.IGNORECASE))
        and not _BUILD_OR_SCAFFOLD_RE.search(cleaned)
    ):
        return _lookup_profile(profiles, INTENT_ARCHITECTURE_DESIGN, default_intent), decision

    return _lookup_profile(profiles, INTENT_COMPLEX_PIPELINE, default_intent), decision


__all__ = ["resolve_profile_with_triage"]
