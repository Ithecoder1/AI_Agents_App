"""Triage Command node for intent classification, skill resolution, and JIT tool provisioning."""

from __future__ import annotations

from typing import Any

from app.application.tools.registry import DynamicToolRegistry
from app.application.tools.retriever import ToolRetriever
from app.application.triage import CascadingTriageService
from app.domain.graph_state import PHASE_ARCHITECT, STATUS_RUNNING, GraphState
from app.domain.ports import IProfileClassifier
from app.infrastructure.orchestration.state import (
    ExecutionLogManager,
    SkillRegistry,
    TriageProfileResolver,
    TriageSnapshot,
)
from app.infrastructure.orchestration.subgraphs.base import BaseGraphNode


class TriageNode(BaseGraphNode):
    """Evaluates prompt intent, attaches modular skills, and provisions JIT tools."""

    def __init__(
        self,
        profile_classifier: IProfileClassifier,
        skill_registry: SkillRegistry,
        log_manager: ExecutionLogManager,
        *,
        tool_registry: DynamicToolRegistry | None = None,
        triage_service: CascadingTriageService | None = None,
        profile_resolver: TriageProfileResolver | None = None,
    ) -> None:
        self._profile_classifier = profile_classifier
        self._skill_registry = skill_registry
        self._log_manager = log_manager
        self._tool_registry = tool_registry
        self._triage_service = triage_service
        self._profile_resolver = profile_resolver

    @property
    def name(self) -> str:
        return "triage_node"

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Classify intent synchronously, resolve skills/tools, and record triage telemetry."""
        req = state.get("user_requirement", "").strip()
        profile, decision = self._profile_classifier.classify_intent_with_decision(
            req,
            triage_service=self._triage_service,
        )

        skill_names = (
            self._profile_resolver.refine_profile_skills(profile, profile.intent)
            if self._profile_resolver is not None
            else tuple(profile.skills)
        )
        resolved_skills = self._skill_registry.resolve_for_profile(skill_names)
        working_state: GraphState = {
            **state,
            "active_skills": list(skill_names),
            "subgraph_results": dict(state.get("subgraph_results") or {}),
        }
        for skill in resolved_skills:
            skill_update = skill.apply_to_state(working_state)
            working_state["active_skills"] = list(skill_update["active_skills"])
            working_state["subgraph_results"] = dict(skill_update["subgraph_results"])

        active_tools_meta: list[dict[str, Any]] = []
        if (
            self._tool_registry is not None
            and profile.task_type != "DIRECT_QA"
            and not profile.tool_policy.is_tool_less
        ):
            retriever = ToolRetriever(self._tool_registry, policy=profile.tool_policy)
            skill_query = self._skill_registry.build_tool_query(
                profile.skills,
                fallback_query=profile.tool_query or req,
            )
            if skill_query:
                retriever.search_available_tools(
                    skill_query,
                    limit=profile.tool_policy.max_active_tools,
                )
            if req:
                retriever.search_available_tools(
                    req,
                    limit=profile.tool_policy.max_active_tools,
                )
            active_tools_meta = [
                {"name": tool.name, "category": tool.category, "summary": tool.summary}
                for tool in retriever.get_active_tools()
            ]

        active_skills = list(working_state.get("active_skills") or [])
        snapshot = TriageSnapshot.from_classification(
            profile=profile,
            decision=decision,
            active_skills=active_skills,
            tools_count=len(active_tools_meta),
        )
        snapshot_dict = snapshot.to_dict()

        log_details = {**snapshot_dict, "active_tools_count": len(active_tools_meta)}
        log_details.pop("tools_count", None)

        logs = self._log_manager.append_to_logs(
            list(working_state.get("execution_logs") or []),
            agent="ceo_triage",
            phase=PHASE_ARCHITECT,
            message=(
                f"Triage: Classified intent='{profile.intent}' "
                f"(task_type='{profile.task_type}', tier={decision.tier}, "
                f"confidence={decision.confidence:.3f}, fast_path={profile.fast_path})."
            ),
            details=log_details,
        )

        sub_results = dict(working_state.get("subgraph_results") or {})
        sub_results["triage"] = snapshot_dict

        return {
            **working_state,
            "intent": profile.intent,
            "task_type": profile.task_type,
            "execution_profile": profile.to_dict(),
            "current_node": self.name,
            "status": STATUS_RUNNING,
            "active_tools": active_tools_meta,
            "subgraph_results": sub_results,
            "execution_logs": logs,
        }

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Execute triage classification without thread-pool overhead."""
        return self.execute(state)


__all__ = ["TriageNode"]
