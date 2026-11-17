"""Zero-tool Direct QA modular skill."""

from __future__ import annotations

from company_core.skills.base import OrchestratorSkill, SkillSpec


class DirectQASkill(OrchestratorSkill):
    """Lightweight fast-path skill for greetings and informational Q&A."""

    def __init__(self) -> None:
        super().__init__(
            SkillSpec(
                name="direct_qa",
                description="Fast-path direct question answering without tool overhead.",
                intent_tags=("DIRECT_QA",),
                tool_query="",
                recommended_tools=(),
                system_directive=(
                    "Answer concisely and directly with zero unnecessary tool invocations "
                    "or architectural scaffolding."
                ),
                token_budget_hint=512,
            )
        )


__all__ = ["DirectQASkill"]
