"""OOP TriageProfileResolver bridging CascadingTriageService with orchestrator profiles."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, ClassVar

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
from app.infrastructure.orchestration.state.skills import SkillRegistry

if TYPE_CHECKING:
    from app.infrastructure.orchestration.state.profiles import (
        OrchestratorConfigLoader,
        OrchestratorProfileConfig,
    )


class TriageProfileResolver:
    """Service resolving CascadingTriageService decisions to execution profiles and skills."""

    _FOCUSED_INSPECT_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(analyze|inspect|audit|search|lint|find in files|check file|review code)\b",
        re.IGNORECASE,
    )
    _MODIFY_OR_TEST_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(fix|bug|refactor|clean up|cleanup|optimize|patch|pytest|test|tests|unit test|"
        r"build|create|scaffold|implement|develop|migration|endpoint)\b",
        re.IGNORECASE,
    )
    _ARCH_ONLY_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(architect|blueprint|design spec|system design|schema design|architecture diagram)\b",
        re.IGNORECASE,
    )
    _BUILD_OR_SCAFFOLD_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(build|create|scaffold|implement|develop|migration|endpoint|service layer|"
        r"from scratch|solve|calculator|evaluator)\b",
        re.IGNORECASE,
    )
    _LEADING_BUILD_PIPELINE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*(build|create|develop|implement|scaffold)\b",
        re.IGNORECASE,
    )
    _LEADING_DESIGN_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*design\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        config_loader: OrchestratorConfigLoader | None = None,
        *,
        profiles: Mapping[str, OrchestratorProfileConfig] | None = None,
        default_intent: str | None = None,
        triage_service: CascadingTriageService | None = None,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        loader = config_loader
        if loader is None and profiles is None:
            from app.infrastructure.orchestration.state.profiles import OrchestratorConfigLoader

            loader = OrchestratorConfigLoader.default()

        self._profiles: Mapping[str, OrchestratorProfileConfig] = (
            profiles if profiles is not None else loader.profiles  # type: ignore[union-attr]
        )
        self._default_intent: str = (
            default_intent
            if default_intent is not None
            else (loader.default_intent if loader is not None else INTENT_COMPLEX_PIPELINE)
        )
        self._triage_service = triage_service
        self._skill_registry = skill_registry or SkillRegistry.default()

    def _lookup_profile(self, intent_key: str) -> OrchestratorProfileConfig:
        if intent_key in self._profiles:
            return self._profiles[intent_key]
        if self._default_intent in self._profiles:
            return self._profiles[self._default_intent]
        return next(iter(self._profiles.values()))

    def resolve_profile(
        self,
        triage_decision: TriageDecision,
        *,
        requirement: str = "",
    ) -> OrchestratorProfileConfig:
        """Map *triage_decision* (with optional *requirement* cues) to an execution profile."""
        cleaned = requirement.strip()

        # Read-only analysis/inspection queries map to FOCUSED_TOOL_TASK when no edit/test verbs
        if (
            triage_decision.intent in ("DIRECT_QA", "CODE_TEST_FIX")
            and self._FOCUSED_INSPECT_RE.search(cleaned)
            and not self._MODIFY_OR_TEST_RE.search(cleaned)
            and INTENT_FOCUSED_TOOL_TASK in self._profiles
        ):
            return self._lookup_profile(INTENT_FOCUSED_TOOL_TASK)

        if triage_decision.intent == "DIRECT_QA":
            return self._lookup_profile(INTENT_DIRECT_QA)

        if (
            self._LEADING_BUILD_PIPELINE_RE.search(cleaned)
            and INTENT_COMPLEX_PIPELINE in self._profiles
        ):
            return self._lookup_profile(INTENT_COMPLEX_PIPELINE)

        if triage_decision.intent == "CODE_TEST_FIX":
            return self._lookup_profile(INTENT_CODE_TEST_FIX)

        # ARCHITECT_PIPELINE -> specialized ARCHITECTURE_DESIGN vs full COMPLEX_PIPELINE
        if (
            INTENT_ARCHITECTURE_DESIGN in self._profiles
            and (self._ARCH_ONLY_RE.search(cleaned) or bool(self._LEADING_DESIGN_RE.match(cleaned)))
            and not self._BUILD_OR_SCAFFOLD_RE.search(cleaned)
        ):
            return self._lookup_profile(INTENT_ARCHITECTURE_DESIGN)

        return self._lookup_profile(INTENT_COMPLEX_PIPELINE)

    def refine_profile_skills(
        self,
        profile: OrchestratorProfileConfig,
        intent: str,
    ) -> tuple[str, ...]:
        """Return deduplicated registered skill names for *profile*, falling back by *intent*."""
        registered = [name for name in profile.skills if self._skill_registry.get(name) is not None]
        if not registered:
            registered = [skill.name for skill in self._skill_registry.find_for_intent(intent)]
        return tuple(dict.fromkeys(registered))

    def classify_and_resolve(
        self,
        requirement: str,
        *,
        triage_service: CascadingTriageService | None = None,
        is_lightweight: bool = False,
    ) -> tuple[OrchestratorProfileConfig, TriageDecision]:
        """Classify *requirement* via ``CascadingTriageService`` and resolve its profile."""
        service = triage_service or self._triage_service or get_triage_service()
        cleaned = requirement.strip()
        decision = service.triage(cleaned)

        if is_lightweight:
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
            return self._lookup_profile(INTENT_DIRECT_QA), decision

        profile = self.resolve_profile(decision, requirement=cleaned)
        return profile, decision


__all__ = ["TriageProfileResolver"]
