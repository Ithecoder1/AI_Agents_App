"""Pure, side-effect-free conditional edge routers for the Master Orchestrator StateGraph."""

from __future__ import annotations

from langgraph.graph import END

from app.domain.graph_state import (
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
    STATUS_WAITING_APPROVAL,
    GraphState,
)


class MasterGraphRouter:
    """Encapsulates pure conditional routing predicates and unified retry bound checks."""

    def __init__(self, *, default_max_retries: int = 3) -> None:
        self._default_max_retries = default_max_retries

    @property
    def default_max_retries(self) -> int:
        """Default retry cap used when ``GraphState.max_retries`` is unset."""
        return self._default_max_retries

    def resolve_retry_bounds(self, state: GraphState) -> tuple[int, int]:
        """Return normalized ``(retry_count, max_retries)`` bounds from ``state``."""
        retries = int(state.get("retry_count", 0))
        raw_limit = state.get("max_retries")
        max_retries = int(raw_limit) if raw_limit is not None else self._default_max_retries
        return retries, max_retries

    def is_retry_exhausted(self, state: GraphState) -> bool:
        """Return ``True`` when the retry counter has reached or exceeded ``max_retries``."""
        retries, max_retries = self.resolve_retry_bounds(state)
        return retries >= max_retries

    def route_after_triage(self, state: GraphState) -> str:
        """Fast-path lightweight ``DIRECT_QA`` queries straight to ``executor_node``."""
        intent = state.get("intent", "")
        task_type = state.get("task_type", "")
        auto_approve = state.get("auto_approve", True)

        if (intent == INTENT_DIRECT_QA or task_type == "DIRECT_QA") and auto_approve:
            return "executor_node"
        return "planner_node"

    def route_after_planner(self, state: GraphState) -> str:
        """Branch after planning: approval gate, focused execution, or sub-graph router."""
        if state.get("status") == STATUS_WAITING_APPROVAL:
            return str(END)

        intent = state.get("intent", "")
        task_type = state.get("task_type", "")
        if intent in (INTENT_DIRECT_QA, INTENT_FOCUSED_TOOL_TASK) or task_type in (
            "DIRECT_QA",
            "FOCUSED_TOOL_TASK",
        ):
            return "executor_node"

        return "subgraph_router_node"

    def route_after_router(self, state: GraphState) -> str:
        """Determine target sub-graph or executor node from active ``task_type`` and artifacts."""
        if state.get("status") == STATUS_WAITING_APPROVAL:
            return str(END)

        task_type = state.get("task_type", "FULL_PIPELINE")
        completed = set(state.get("completed_tasks") or [])

        if task_type in ("DIRECT_QA", "FOCUSED_TOOL_TASK"):
            return "executor_node"

        if task_type == "ARCHITECTURE":
            return "architect_subgraph"

        if task_type == "CODE_TEST_FIX":
            return "coder_qa_subgraph"

        if "architecture" not in completed or not state.get("architecture_spec"):
            return "architect_subgraph"

        if "qa" not in completed:
            return "coder_qa_subgraph"

        return str(END)

    def route_after_architect(self, state: GraphState) -> str:
        """Continue to ``coder_qa_subgraph`` only if required by active profile or task type."""
        profile = state.get("execution_profile") or {}
        subgraphs = profile.get("subgraphs")
        if isinstance(subgraphs, (list, tuple)):
            return "coder_qa_subgraph" if "coder_qa_subgraph" in subgraphs else str(END)

        task_type = state.get("task_type", "FULL_PIPELINE")
        if task_type == "ARCHITECTURE":
            return str(END)
        return "coder_qa_subgraph"

    def route_after_coder_qa(self, state: GraphState) -> str:
        """Evaluate ``coder_qa_subgraph`` outcome: completion vs task decomposition."""
        test_results = state.get("test_results") or ""
        if "PASSED" in test_results:
            return str(END)

        if self.is_retry_exhausted(state):
            return "task_decomposer"

        return str(END)


_DEFAULT_ROUTER = MasterGraphRouter()


def route_after_triage(state: GraphState) -> str:
    """Pure routing function delegating to ``MasterGraphRouter.route_after_triage``."""
    return _DEFAULT_ROUTER.route_after_triage(state)


def route_after_planner(state: GraphState) -> str:
    """Pure routing function delegating to ``MasterGraphRouter.route_after_planner``."""
    return _DEFAULT_ROUTER.route_after_planner(state)


def route_after_router(state: GraphState) -> str:
    """Pure routing function delegating to ``MasterGraphRouter.route_after_router``."""
    return _DEFAULT_ROUTER.route_after_router(state)


def route_after_architect(state: GraphState) -> str:
    """Pure routing function delegating to ``MasterGraphRouter.route_after_architect``."""
    return _DEFAULT_ROUTER.route_after_architect(state)


def route_after_coder_qa(state: GraphState) -> str:
    """Pure routing function delegating to ``MasterGraphRouter.route_after_coder_qa``."""
    return _DEFAULT_ROUTER.route_after_coder_qa(state)


__all__ = [
    "MasterGraphRouter",
    "route_after_architect",
    "route_after_coder_qa",
    "route_after_planner",
    "route_after_router",
    "route_after_triage",
]
