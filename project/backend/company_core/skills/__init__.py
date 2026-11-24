"""DEPRECATED: Compatibility shim for Modular Skills Layer.

Prefer ``app.infrastructure.orchestration.state.skills``.
"""

from __future__ import annotations

__deprecated__ = True

from app.infrastructure.orchestration.state.skills import (
    ArchitecturePlanningSkill,
    BaseOrchestratorSkill,
    CodeAnalysisSkill,
    CodeVerificationSkill,
    DirectQASkill,
    OrchestratorSkill,
    SkillRegistry,
    SkillSpec,
)

get_default_skill_registry = SkillRegistry.default

__all__ = [
    "ArchitecturePlanningSkill",
    "BaseOrchestratorSkill",
    "CodeAnalysisSkill",
    "CodeVerificationSkill",
    "DirectQASkill",
    "OrchestratorSkill",
    "SkillRegistry",
    "SkillSpec",
    "get_default_skill_registry",
]
