"""Abstract Command-node and Sub-Graph Builder contracts for LangGraph orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, cast

from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domain.graph_state import GraphState


class BaseGraphNode(ABC):
    """Command-pattern callable graph node with explicit dependency injection."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique LangGraph node identifier."""

    @abstractmethod
    def execute(self, state: GraphState) -> dict[str, Any]:
        """Synchronously execute the node logic and return partial or full state update."""

    @abstractmethod
    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Asynchronously execute the node as a callable service command."""

    def as_runnable(self) -> RunnableLambda[Any, Any]:
        """Expose dual sync/async runnable supporting both ``invoke`` and ``ainvoke``."""
        return RunnableLambda(self.execute, afunc=self)


class BaseSubGraphBuilder(ABC):
    """Builder-pattern base class encapsulating LangGraph StateGraph wiring and compilation."""

    @abstractmethod
    def build(self) -> StateGraph[Any, Any, Any, Any]:
        """Construct and wire all Command nodes and edges in an uncompiled ``StateGraph``."""

    def compile(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        """Compile the constructed ``StateGraph`` ready for standalone or nested execution."""
        return self.build().compile()

    @staticmethod
    def _register_node(graph: StateGraph[Any, Any, Any, Any], node: BaseGraphNode) -> None:
        graph.add_node(node.name, cast("Any", node.as_runnable()))


__all__ = [
    "BaseGraphNode",
    "BaseSubGraphBuilder",
]
