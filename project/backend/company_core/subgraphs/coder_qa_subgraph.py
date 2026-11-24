"""DEPRECATED: Compatibility shim for the OOP Code-Test-Fix sub-graph.

Prefer ``app.infrastructure.orchestration.CoderQASubGraphBuilder``.
"""

from __future__ import annotations

__deprecated__ = True

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from app.application.tools.registry import DynamicToolRegistry
from app.domain.ports import TestRunResult
from app.infrastructure.orchestration.adapters import LocalCodeWorkspaceRunner
from app.infrastructure.orchestration.subgraphs.coder_qa import (
    CoderNode,
    CoderQARoutePolicy,
    CoderQASubGraphBuilder,
    CodeSynthesisService,
    DevOpsEnvironmentService,
    DevOpsTestNode,
    EvaluatorNode,
    TaskDecompositionStrategy,
    TestResultEvaluator,
)
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from company_core.state import GraphState


class _LegacyCompanyCoreWorkspaceAdapter:
    """Adapts ``ICodeWorkspaceRunner`` to preserve legacy ``run_pytest_suite`` monkeypatches."""

    def __init__(
        self,
        project_root_resolver: Callable[[str], Path] | None = None,
        file_runner: LocalCodeWorkspaceRunner | None = None,
    ) -> None:
        self._project_root_resolver = project_root_resolver
        self._file_runner = file_runner if file_runner is not None else LocalCodeWorkspaceRunner()

    def resolve_project_dir(self, project_id: str) -> Path:
        if self._project_root_resolver is not None:
            return self._project_root_resolver(project_id)
        base = Path.cwd() / "projects" / project_id
        base.mkdir(parents=True, exist_ok=True)
        return base

    def write_files(self, target_dir: Path, files: Mapping[str, str]) -> list[str]:
        return self._file_runner.write_files(target_dir, files)

    def read_files(
        self,
        target_dir: Path,
        extensions: tuple[str, ...] = (".py", ".json", ".yaml", ".yml", ".md", ".txt"),
    ) -> dict[str, str]:
        return self._file_runner.read_files(target_dir, extensions=extensions)

    def summarize_code(
        self,
        files: Mapping[str, str],
        max_lines_per_file: int = 50,
        strip_comments: bool = True,
    ) -> str:
        return self._file_runner.summarize_code(
            files,
            max_lines_per_file=max_lines_per_file,
            strip_comments=strip_comments,
        )

    def run_tests(self, target_dir: Path, timeout_s: float = 30.0) -> TestRunResult:
        from company_core.agents import quality_assurance as qa_module

        qa_runner = cast(
            "Callable[..., tuple[bool, str, str]]",
            vars(qa_module)["run_pytest_suite"],
        )
        success, combined_output, clean_traceback = qa_runner(
            target_dir,
            timeout_s=timeout_s,
        )
        if success:
            return TestRunResult.passed(combined_output or "All tests passed.")
        return TestRunResult.failed(
            combined_output or clean_traceback,
            clean_traceback=clean_traceback or None,
        )


def _build_legacy_builder(
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int = 3,
    timeout_s: float = 30.0,
) -> CoderQASubGraphBuilder:
    adapter = _LegacyCompanyCoreWorkspaceAdapter(project_root_resolver=project_root_resolver)
    return CoderQASubGraphBuilder(
        workspace_runner=adapter,
        tool_registry=tool_registry,
        simulate_initial_bug=simulate_initial_bug,
        max_retries=max_retries,
        timeout_s=timeout_s,
    )


def make_coder_node(
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    simulate_initial_bug: bool = False,
) -> Callable[[GraphState], GraphState]:
    builder = _build_legacy_builder(
        project_root_resolver=project_root_resolver,
        tool_registry=tool_registry,
        simulate_initial_bug=simulate_initial_bug,
    )
    return cast("Callable[[GraphState], GraphState]", builder.coder_node.execute)


def make_devops_test_node(
    project_root_resolver: Callable[[str], Path] | None = None,
) -> Callable[[GraphState], GraphState]:
    builder = _build_legacy_builder(project_root_resolver=project_root_resolver)
    return cast("Callable[[GraphState], GraphState]", builder.devops_test_node.execute)


def make_evaluator_node(
    project_root_resolver: Callable[[str], Path] | None = None,
    timeout_s: float = 30.0,
) -> Callable[[GraphState], GraphState]:
    builder = _build_legacy_builder(
        project_root_resolver=project_root_resolver,
        timeout_s=timeout_s,
    )
    return cast("Callable[[GraphState], GraphState]", builder.evaluator_node.execute)


def make_coder_qa_route(max_retries: int = 3) -> Callable[[GraphState], str]:
    return CoderQARoutePolicy(default_max_retries=max_retries)


def create_coder_qa_subgraph(
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int = 3,
) -> StateGraph[Any, Any, Any, Any]:
    return _build_legacy_builder(
        project_root_resolver=project_root_resolver,
        tool_registry=tool_registry,
        simulate_initial_bug=simulate_initial_bug,
        max_retries=max_retries,
    ).build()


def compile_coder_qa_subgraph(
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int = 3,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    return _build_legacy_builder(
        project_root_resolver=project_root_resolver,
        tool_registry=tool_registry,
        simulate_initial_bug=simulate_initial_bug,
        max_retries=max_retries,
    ).compile()


__all__ = [
    "CodeSynthesisService",
    "CoderNode",
    "CoderQARoutePolicy",
    "CoderQASubGraphBuilder",
    "DevOpsEnvironmentService",
    "DevOpsTestNode",
    "EvaluatorNode",
    "TaskDecompositionStrategy",
    "TestResultEvaluator",
    "compile_coder_qa_subgraph",
    "create_coder_qa_subgraph",
    "make_coder_node",
    "make_coder_qa_route",
    "make_devops_test_node",
    "make_evaluator_node",
]
