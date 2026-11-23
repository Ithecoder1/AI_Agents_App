"""Backward-compatibility shim for modular orchestrator skill contracts and registry."""

from __future__ import annotations

from app.infrastructure.orchestration.state.skills import (
    OrchestratorSkill,
    SkillRegistry,
    SkillSpec,
)

__all__ = [
    "OrchestratorSkill",
    "SkillRegistry",
    "SkillSpec",
]
