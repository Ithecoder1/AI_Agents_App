"""Backward-compatibility shim for Modular Skills Layer."""

from __future__ import annotations

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
