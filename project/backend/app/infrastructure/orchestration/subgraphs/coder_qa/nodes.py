"""Command-pattern callable graph nodes for the Code-Test-Fix sub-graph."""

from __future__ import annotations

import asyncio
from typing import Any

from app.application.tools.registry import DynamicToolRegistry
from app.application.tools.retriever import ToolRetriever
from app.domain.graph_state import PHASE_CODER, PHASE_DEVOPS, PHASE_QA, GraphState
from app.domain.ports import ICodeWorkspaceRunner
from app.infrastructure.orchestration.state import ExecutionLogManager
from app.infrastructure.orchestration.subgraphs.base import BaseGraphNode
from app.infrastructure.orchestration.subgraphs.coder_qa.services import (
    CodeSynthesisService,
    DevOpsEnvironmentService,
    TestResultEvaluator,
)


class CoderNode(BaseGraphNode):
    """Synthesizes or self-corrects source files via an injected ``ICodeWorkspaceRunner``."""

    def __init__(
        self,
        workspace_runner: ICodeWorkspaceRunner,
        synthesis_service: CodeSynthesisService,
        log_manager: ExecutionLogManager,
        *,
        tool_registry: DynamicToolRegistry | None = None,
        simulate_initial_bug: bool = False,
    ) -> None:
        self._workspace_runner = workspace_runner
        self._synthesis_service = synthesis_service
        self._log_manager = log_manager
        self._tool_registry = tool_registry
        self._simulate_initial_bug = simulate_initial_bug

    @property
    def name(self) -> str:
        return "coder_node"

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Generate or patch code, persist via ``ICodeWorkspaceRunner``, and update state."""
        project_id = state.get("project_id", "vibe-app")
        project_dir = self._workspace_runner.resolve_project_dir(project_id)
        retry_count = int(state.get("retry_count", 0))
        has_error = bool(state.get("error_message"))

        active_tools_meta: list[dict[str, Any]] = []
        if self._tool_registry is not None:
            retriever = ToolRetriever(self._tool_registry)
            tool_matches = retriever.search_available_tools("file write view edit")
            active_tools_meta = [
                {"name": t["name"], "category": t["category"], "summary": t["summary"]}
                for t in tool_matches
            ]

        existing_files = dict(state.get("code_files") or {})
        req = state.get("user_requirement", "")
        if has_error and retry_count > 0:
            error_msg = state.get("error_message") or ""
            generated = self._synthesis_service.apply_self_correction(req, error_msg)
            log_msg = (
                "Coder: Applying self-correction patch based on QA traceback "
                f"(Attempt #{retry_count})."
            )
        else:
            generated = self._synthesis_service.synthesize(
                req,
                simulate_bug=self._simulate_initial_bug,
            )
            log_msg = "Coder: Implemented core source files and test fixtures."

        merged_files = {**existing_files, **generated}
        self._workspace_runner.write_files(project_dir, merged_files)

        completed = list(state.get("completed_tasks") or [])
        if "code" not in completed:
            completed.append("code")

        logs = self._log_manager.append_to_logs(
            list(state.get("execution_logs") or []),
            agent="coder",
            phase=PHASE_CODER,
            message=log_msg,
            details={"written_files": list(generated.keys()), "attempt": retry_count},
        )

        sub_results = dict(state.get("subgraph_results") or {})
        sub_results["coder"] = {
            "written_files": list(generated.keys()),
            "retry_count": retry_count,
            "status": "ready_for_test",
        }

        return {
            **state,
            "code_files": merged_files,
            "completed_tasks": completed,
            "active_tools": active_tools_meta,
            "subgraph_results": sub_results,
            "current_node": self.name,
            "current_phase": PHASE_DEVOPS,
            "execution_logs": logs,
        }

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Offload workspace code synthesis and disk writes to a worker thread."""
        return await asyncio.to_thread(self.execute, state)


class DevOpsTestNode(BaseGraphNode):
    """Provisions workspace requirements and harness artifacts via ``ICodeWorkspaceRunner``."""

    def __init__(
        self,
        workspace_runner: ICodeWorkspaceRunner,
        devops_service: DevOpsEnvironmentService,
        log_manager: ExecutionLogManager,
    ) -> None:
        self._workspace_runner = workspace_runner
        self._devops_service = devops_service
        self._log_manager = log_manager

    @property
    def name(self) -> str:
        return "devops_test_node"

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Write DevOps environment files into the workspace and advance to QA phase."""
        project_id = state.get("project_id", "vibe-app")
        project_dir = self._workspace_runner.resolve_project_dir(project_id)
        devops_files = self._devops_service.build_environment_files(project_id)
        self._workspace_runner.write_files(project_dir, devops_files)

        merged_files = {**dict(state.get("code_files") or {}), **devops_files}
        completed = list(state.get("completed_tasks") or [])
        if "devops" not in completed:
            completed.append("devops")

        logs = self._log_manager.append_to_logs(
            list(state.get("execution_logs") or []),
            agent="devops",
            phase=PHASE_DEVOPS,
            message="DevOps: Configured requirements.txt and pytest execution harness.",
            details={"configured_files": list(devops_files.keys())},
        )

        sub_results = dict(state.get("subgraph_results") or {})
        sub_results["devops"] = {"status": "configured"}

        return {
            **state,
            "code_files": merged_files,
            "completed_tasks": completed,
            "current_node": self.name,
            "current_phase": PHASE_QA,
            "subgraph_results": sub_results,
            "execution_logs": logs,
        }

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Offload workspace DevOps file provisioning to a worker thread."""
        return await asyncio.to_thread(self.execute, state)


class EvaluatorNode(BaseGraphNode):
    """Executes automated test suite through ``ICodeWorkspaceRunner`` and evaluates verdict."""

    def __init__(
        self,
        workspace_runner: ICodeWorkspaceRunner,
        evaluator: TestResultEvaluator,
        log_manager: ExecutionLogManager,
        *,
        timeout_s: float = 30.0,
    ) -> None:
        self._workspace_runner = workspace_runner
        self._evaluator = evaluator
        self._log_manager = log_manager
        self._timeout_s = timeout_s

    @property
    def name(self) -> str:
        return "evaluator_node"

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Run workspace tests synchronously and translate outcome into state transitions."""
        project_id = state.get("project_id", "vibe-app")
        project_dir = self._workspace_runner.resolve_project_dir(project_id)
        run_result = self._workspace_runner.run_tests(project_dir, timeout_s=self._timeout_s)
        return self._evaluator.evaluate(state, run_result, self._log_manager)

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Offload test execution and verdict evaluation to a worker thread."""
        return await asyncio.to_thread(self.execute, state)


__all__ = [
    "CoderNode",
    "DevOpsTestNode",
    "EvaluatorNode",
]
