"""Test-driven code synthesis and self-correction verification skill."""

from __future__ import annotations

from company_core.skills.base import OrchestratorSkill, SkillSpec


class CodeVerificationSkill(OrchestratorSkill):
    """Capability unit for code implementation, pytest verification, and patching."""

    def __init__(self) -> None:
        super().__init__(
            SkillSpec(
                name="code_verification",
                description="Implementation synthesis, pytest harness setup, and self-correction.",
                intent_tags=("CODE_TEST_FIX", "COMPLEX_PIPELINE"),
                tool_query="file write view edit run command pytest",
                recommended_tools=(
                    "write_to_file",
                    "replace_file_content",
                    "run_command",
                    "view_file",
                ),
                system_directive=(
                    "Synthesize minimal, well-tested Python modules, run pytest suites, "
                    "and apply surgical fixes from traceback feedback."
                ),
                token_budget_hint=4096,
            )
        )


__all__ = ["CodeVerificationSkill"]
