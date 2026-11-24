"""Builder-pattern assembler for the Architecture Design LangGraph sub-graph."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.infrastructure.orchestration.state import CompanyState, ExecutionLogManager
from app.infrastructure.orchestration.subgraphs.architect.nodes import (
    BlueprintGeneratorNode,
    RequirementsParserNode,
    SchemaValidatorNode,
)
from app.infrastructure.orchestration.subgraphs.architect.services import (
    BlueprintGeneratorService,
    BlueprintSchemaValidator,
    RequirementsParserService,
)
from app.infrastructure.orchestration.subgraphs.base import BaseSubGraphBuilder


class ArchitectSubGraphBuilder(BaseSubGraphBuilder):
    """Constructs and compiles the isolated Architecture Design sub-graph."""

    def __init__(
        self,
        *,
        parser_service: RequirementsParserService | None = None,
        generator_service: BlueprintGeneratorService | None = None,
        validator_service: BlueprintSchemaValidator | None = None,
        log_manager: ExecutionLogManager | None = None,
    ) -> None:
        self._log_manager = log_manager if log_manager is not None else ExecutionLogManager()
        self._parser_service = (
            parser_service if parser_service is not None else RequirementsParserService()
        )
        self._generator_service = (
            generator_service if generator_service is not None else BlueprintGeneratorService()
        )
        self._validator_service = (
            validator_service if validator_service is not None else BlueprintSchemaValidator()
        )

        self._requirements_parser_node = RequirementsParserNode(
            parser=self._parser_service,
            log_manager=self._log_manager,
        )
        self._blueprint_generator_node = BlueprintGeneratorNode(
            generator=self._generator_service,
            log_manager=self._log_manager,
        )
        self._schema_validator_node = SchemaValidatorNode(
            validator=self._validator_service,
            log_manager=self._log_manager,
        )

    @property
    def requirements_parser_node(self) -> RequirementsParserNode:
        """Configured requirements parser node instance."""
        return self._requirements_parser_node

    @property
    def blueprint_generator_node(self) -> BlueprintGeneratorNode:
        """Configured blueprint generator node instance."""
        return self._blueprint_generator_node

    @property
    def schema_validator_node(self) -> SchemaValidatorNode:
        """Configured schema validator node instance."""
        return self._schema_validator_node

    def build(self) -> StateGraph[Any, Any, Any, Any]:
        """Build the ``Architect`` StateGraph wiring parser, generator, and validator nodes."""
        subgraph: StateGraph[Any, Any, Any, Any] = StateGraph(CompanyState)

        self._register_node(subgraph, self._requirements_parser_node)
        self._register_node(subgraph, self._blueprint_generator_node)
        self._register_node(subgraph, self._schema_validator_node)

        subgraph.set_entry_point(self._requirements_parser_node.name)
        subgraph.add_edge(self._requirements_parser_node.name, self._blueprint_generator_node.name)
        subgraph.add_edge(self._blueprint_generator_node.name, self._schema_validator_node.name)
        subgraph.add_edge(self._schema_validator_node.name, END)
        return subgraph


__all__ = ["ArchitectSubGraphBuilder"]
