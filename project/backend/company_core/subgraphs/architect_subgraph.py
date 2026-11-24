"""DEPRECATED: Compatibility shim for the OOP Architecture Design sub-graph.

Prefer ``app.infrastructure.orchestration.ArchitectSubGraphBuilder``.
"""

from __future__ import annotations

__deprecated__ = True

from typing import Any, cast

from app.infrastructure.orchestration.subgraphs.architect import (
    ArchitectSubGraphBuilder,
    BlueprintGeneratorNode,
    BlueprintGeneratorService,
    BlueprintSchemaValidator,
    RequirementsParserNode,
    RequirementsParserService,
    SchemaValidatorNode,
)
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from company_core.state import GraphState

_DEFAULT_BUILDER = ArchitectSubGraphBuilder()


def requirements_parser_node(state: GraphState) -> GraphState:
    return cast("GraphState", _DEFAULT_BUILDER.requirements_parser_node.execute(state))


def blueprint_generator_node(state: GraphState) -> GraphState:
    return cast("GraphState", _DEFAULT_BUILDER.blueprint_generator_node.execute(state))


def schema_validator_node(state: GraphState) -> GraphState:
    return cast("GraphState", _DEFAULT_BUILDER.schema_validator_node.execute(state))


def create_architect_subgraph() -> StateGraph[Any, Any, Any, Any]:
    return _DEFAULT_BUILDER.build()


def compile_architect_subgraph() -> CompiledStateGraph[Any, Any, Any, Any]:
    return _DEFAULT_BUILDER.compile()


__all__ = [
    "ArchitectSubGraphBuilder",
    "BlueprintGeneratorNode",
    "BlueprintGeneratorService",
    "BlueprintSchemaValidator",
    "RequirementsParserNode",
    "RequirementsParserService",
    "SchemaValidatorNode",
    "blueprint_generator_node",
    "compile_architect_subgraph",
    "create_architect_subgraph",
    "requirements_parser_node",
    "schema_validator_node",
]
