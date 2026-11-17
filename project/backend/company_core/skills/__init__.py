"""Modular Skills Layer for Dynamic, Intent-Driven LangGraph Orchestration."""

from __future__ import annotations

from company_core.skills.architecture_planning import ArchitecturePlanningSkill
from company_core.skills.base import OrchestratorSkill, SkillRegistry, SkillSpec
from company_core.skills.code_analysis import CodeAnalysisSkill
from company_core.skills.code_verification import CodeVerificationSkill
from company_core.skills.direct_qa import DirectQASkill

_DEFAULT_SKILL_REGISTRY: SkillRegistry | None = None


def get_default_skill_registry() -> SkillRegistry:
    """Return the lazily populated default SkillRegistry singleton."""
    global _DEFAULT_SKILL_REGISTRY
    if _DEFAULT_SKILL_REGISTRY is None:
        registry = SkillRegistry()
        registry.register(DirectQASkill())
        registry.register(CodeAnalysisSkill())
        registry.register(ArchitecturePlanningSkill())
        registry.register(CodeVerificationSkill())
        _DEFAULT_SKILL_REGISTRY = registry
    return _DEFAULT_SKILL_REGISTRY


__all__ = [
    "ArchitecturePlanningSkill",
    "CodeAnalysisSkill",
    "CodeVerificationSkill",
    "DirectQASkill",
    "OrchestratorSkill",
    "SkillRegistry",
    "SkillSpec",
    "get_default_skill_registry",
]
