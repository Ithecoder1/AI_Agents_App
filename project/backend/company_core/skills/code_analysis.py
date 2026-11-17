"""Focused codebase inspection and analysis modular skill."""

from __future__ import annotations

from company_core.skills.base import OrchestratorSkill, SkillSpec


class CodeAnalysisSkill(OrchestratorSkill):
    """Focused inspection skill provisioning read-only search/view capabilities."""

    def __init__(self) -> None:
        super().__init__(
            SkillSpec(
                name="code_analysis",
                description="Read-only codebase inspection, search, and static review.",
                intent_tags=("FOCUSED_TOOL_TASK", "COMPLEX_PIPELINE"),
                tool_query="file view search grep list",
                recommended_tools=("view_file", "grep_search", "list_dir"),
                system_directive=(
                    "Inspect targeted files and symbols concisely before synthesizing findings. "
                    "Do not mutate files unless explicitly instructed."
                ),
                token_budget_hint=1536,
            )
        )


__all__ = ["CodeAnalysisSkill"]
