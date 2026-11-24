"""Abstract skill contracts, modular implementations, and encapsulated SkillRegistry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from app.domain.graph_state import GraphState


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """Immutable, validated specification of a modular orchestrator capability."""

    name: str
    description: str
    intent_tags: tuple[str, ...]
    tool_query: str = ""
    recommended_tools: tuple[str, ...] = ()
    system_directive: str = ""
    token_budget_hint: int = 1024

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("SkillSpec 'name' must not be empty")
        if not self.description.strip():
            raise ValueError("SkillSpec 'description' must not be empty")
        if self.token_budget_hint <= 0:
            raise ValueError(
                f"SkillSpec 'token_budget_hint' must be positive, got {self.token_budget_hint!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize skill metadata to a JSON-compatible dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "intent_tags": list(self.intent_tags),
            "tool_query": self.tool_query,
            "recommended_tools": list(self.recommended_tools),
            "system_directive": self.system_directive,
            "token_budget_hint": self.token_budget_hint,
        }


class BaseOrchestratorSkill(ABC):
    """Abstract base class standardizing all modular orchestrator skills."""

    def __init__(self, spec: SkillSpec) -> None:
        self._spec = spec

    @property
    def name(self) -> str:
        """Canonical unique name of the skill."""
        return self._spec.name

    @property
    def spec(self) -> SkillSpec:
        """Return immutable SkillSpec metadata."""
        return self._spec

    @abstractmethod
    def can_handle(self, intent: str) -> bool:
        """Return True if this skill applies to the given orchestration *intent*."""
        raise NotImplementedError

    @abstractmethod
    def execute(self, state: GraphState) -> dict[str, Any]:
        """Apply this skill's state update to *state* and return changed state fields."""
        raise NotImplementedError

    def apply_to_state(self, state: GraphState) -> dict[str, Any]:
        """Alias delegating to :meth:`execute` for backward-compatible callers."""
        return self.execute(state)


class OrchestratorSkill(BaseOrchestratorSkill):
    """Standard metadata-backed skill attaching directives and specs to GraphState."""

    def can_handle(self, intent: str) -> bool:
        """Check whether *intent* matches any configured ``intent_tags``."""
        normalized = intent.strip().upper()
        if not normalized:
            return False
        return normalized in {tag.strip().upper() for tag in self._spec.intent_tags}

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Attach skill metadata into GraphState subgraph_results without overwriting siblings."""
        active_skills = list(state.get("active_skills") or [])
        if self.name not in active_skills:
            active_skills.append(self.name)

        sub_results = dict(state.get("subgraph_results") or {})
        applied = dict(sub_results.get("skills_applied") or {})
        applied[self.name] = self._spec.to_dict()
        sub_results["skills_applied"] = applied

        return {
            "active_skills": active_skills,
            "subgraph_results": sub_results,
        }


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


class SkillRegistry:
    """Encapsulated registry of modular orchestrator skills."""

    _default_instance: ClassVar[SkillRegistry | None] = None

    def __init__(self) -> None:
        self._skills: dict[str, BaseOrchestratorSkill] = {}

    @classmethod
    def default(cls) -> SkillRegistry:
        """Return the lazily populated default SkillRegistry singleton."""
        if cls._default_instance is None:
            registry = cls()
            registry.register(DirectQASkill())
            registry.register(CodeAnalysisSkill())
            registry.register(ArchitecturePlanningSkill())
            registry.register(CodeVerificationSkill())
            cls._default_instance = registry
        return cls._default_instance

    @classmethod
    def reset_default(cls) -> None:
        """Clear the cached default singleton (useful for isolated tests)."""
        cls._default_instance = None

    def register(self, skill: BaseOrchestratorSkill) -> None:
        """Register or replace a skill instance keyed by its canonical name."""
        self._skills[skill.name] = skill

    def get(self, name: str) -> BaseOrchestratorSkill | None:
        """Retrieve a registered skill by name, or None if missing."""
        return self._skills.get(name)

    def list_available(self) -> tuple[BaseOrchestratorSkill, ...]:
        """Return all registered skills in registration order."""
        return tuple(self._skills.values())

    def list_skills(self) -> tuple[BaseOrchestratorSkill, ...]:
        """Alias for :meth:`list_available`."""
        return self.list_available()

    def find_for_intent(self, intent: str) -> tuple[BaseOrchestratorSkill, ...]:
        """Return all registered skills capable of handling *intent*."""
        return tuple(skill for skill in self._skills.values() if skill.can_handle(intent))

    def resolve_for_profile(self, skill_names: Sequence[str]) -> list[BaseOrchestratorSkill]:
        """Resolve all known skills listed in an execution profile."""
        resolved: list[BaseOrchestratorSkill] = []
        for name in skill_names:
            skill = self._skills.get(name)
            if skill is not None:
                resolved.append(skill)
        return resolved

    def build_tool_query(
        self,
        skill_names: Sequence[str],
        fallback_query: str = "",
    ) -> str:
        """Combine JIT tool search terms across all active profile skills."""
        terms: list[str] = []
        seen: set[str] = set()
        for skill in self.resolve_for_profile(skill_names):
            for token in skill.spec.tool_query.split():
                if token not in seen:
                    seen.add(token)
                    terms.append(token)
        if not terms and fallback_query:
            return fallback_query.strip()
        return " ".join(terms)

    def collect_directives(self, skill_names: Sequence[str]) -> str:
        """Assemble combined system instructions for all active skills."""
        directives = [
            skill.spec.system_directive
            for skill in self.resolve_for_profile(skill_names)
            if skill.spec.system_directive
        ]
        return "\n".join(directives)


__all__ = [
    "ArchitecturePlanningSkill",
    "BaseOrchestratorSkill",
    "CodeAnalysisSkill",
    "CodeVerificationSkill",
    "DirectQASkill",
    "OrchestratorSkill",
    "SkillRegistry",
    "SkillSpec",
]
