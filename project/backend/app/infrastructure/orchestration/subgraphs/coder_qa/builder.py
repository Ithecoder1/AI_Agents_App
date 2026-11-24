"""Builder-pattern assembler for the Code-Test-Fix LangGraph sub-graph."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.application.tools.registry import DynamicToolRegistry
from app.domain.ports import ICodeWorkspaceRunner
from app.infrastructure.orchestration.state import CompanyState, ExecutionLogManager
from app.infrastructure.orchestration.subgraphs.base import BaseSubGraphBuilder
from app.infrastructure.orchestration.subgraphs.coder_qa.nodes import (
    CoderNode,
    DevOpsTestNode,
    EvaluatorNode,
)
from app.infrastructure.orchestration.subgraphs.coder_qa.services import (
    CoderQARoutePolicy,
    CodeSynthesisService,
    DevOpsEnvironmentService,
    TestResultEvaluator,
)


class CoderQASubGraphBuilder(BaseSubGraphBuilder):
    """Constructs and compiles the Code-Test-Fix LangGraph sub-graph via DI."""

    def __init__(
        self,
        workspace_runner: ICodeWorkspaceRunner,
        *,
        synthesis_service: CodeSynthesisService | None = None,
        devops_service: DevOpsEnvironmentService | None = None,
        evaluator_service: TestResultEvaluator | None = None,
        route_policy: CoderQARoutePolicy | None = None,
        log_manager: ExecutionLogManager | None = None,
        tool_registry: DynamicToolRegistry | None = None,
        simulate_initial_bug: bool = False,
        max_retries: int = 3,
        timeout_s: float = 30.0,
    ) -> None:
        self._workspace_runner = workspace_runner
        self._log_manager = log_manager if log_manager is not None else ExecutionLogManager()
        self._synthesis_service = (
            synthesis_service if synthesis_service is not None else CodeSynthesisService()
        )
        self._devops_service = (
            devops_service if devops_service is not None else DevOpsEnvironmentService()
        )
        self._evaluator_service = (
            evaluator_service if evaluator_service is not None else TestResultEvaluator()
        )
        self._route_policy = (
            route_policy
            if route_policy is not None
            else CoderQARoutePolicy(default_max_retries=max_retries)
        )

        self._coder_node = CoderNode(
            workspace_runner=self._workspace_runner,
            synthesis_service=self._synthesis_service,
            log_manager=self._log_manager,
            tool_registry=tool_registry,
            simulate_initial_bug=simulate_initial_bug,
        )
        self._devops_test_node = DevOpsTestNode(
            workspace_runner=self._workspace_runner,
            devops_service=self._devops_service,
            log_manager=self._log_manager,
        )
        self._evaluator_node = EvaluatorNode(
            workspace_runner=self._workspace_runner,
            evaluator=self._evaluator_service,
            log_manager=self._log_manager,
            timeout_s=timeout_s,
        )

    @property
    def coder_node(self) -> CoderNode:
        """Configured coder Command node instance."""
        return self._coder_node

    @property
    def devops_test_node(self) -> DevOpsTestNode:
        """Configured DevOps harness Command node instance."""
        return self._devops_test_node

    @property
    def evaluator_node(self) -> EvaluatorNode:
        """Configured QA evaluator Command node instance."""
        return self._evaluator_node

    @property
    def route_policy(self) -> CoderQARoutePolicy:
        """Configured conditional routing policy after QA evaluation."""
        return self._route_policy

    def build(self) -> StateGraph[Any, Any, Any, Any]:
        """Build the Code-Test-Fix ``StateGraph`` with self-correction loop edges."""
        subgraph: StateGraph[Any, Any, Any, Any] = StateGraph(CompanyState)

        self._register_node(subgraph, self._coder_node)
        self._register_node(subgraph, self._devops_test_node)
        self._register_node(subgraph, self._evaluator_node)

        subgraph.set_entry_point(self._coder_node.name)
        subgraph.add_edge(self._coder_node.name, self._devops_test_node.name)
        subgraph.add_edge(self._devops_test_node.name, self._evaluator_node.name)
        subgraph.add_conditional_edges(
            self._evaluator_node.name,
            self._route_policy,
            {
                self._coder_node.name: self._coder_node.name,
                END: END,
            },
        )
        return subgraph


__all__ = ["CoderQASubGraphBuilder"]
