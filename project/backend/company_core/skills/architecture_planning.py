"""Architecture planning and hexagonal boundary validation skill."""

from __future__ import annotations

from company_core.skills.base import OrchestratorSkill, SkillSpec


class ArchitecturePlanningSkill(OrchestratorSkill):
    """Capability unit for modular system blueprints and schema design."""

    def __init__(self) -> None:
        super().__init__(
            SkillSpec(
                name="architecture_planning",
                description="Modular Clean/Hexagonal system architecture synthesis and validation.",
                intent_tags=("ARCHITECTURE_DESIGN", "COMPLEX_PIPELINE"),
                tool_query="file view search architecture",
                recommended_tools=("view_file", "list_dir"),
                system_directive=(
                    "Design clean component boundaries, dependency directions, and testable "
                    "contracts following Hexagonal/Clean Architecture."
                ),
                token_budget_hint=2048,
            )
        )


__all__ = ["ArchitecturePlanningSkill"]
