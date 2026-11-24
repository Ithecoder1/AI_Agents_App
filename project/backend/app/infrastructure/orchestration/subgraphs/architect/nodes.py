"""Command-pattern callable graph nodes for the Architecture Design sub-graph."""

from __future__ import annotations

from typing import Any

from app.domain.graph_state import (
    PHASE_ARCHITECT,
    PHASE_COMPLETED,
    STATUS_COMPLETED,
    GraphState,
)
from app.infrastructure.orchestration.state import ExecutionLogManager
from app.infrastructure.orchestration.subgraphs.architect.services import (
    BlueprintGeneratorService,
    BlueprintSchemaValidator,
    RequirementsParserService,
)
from app.infrastructure.orchestration.subgraphs.base import BaseGraphNode


class RequirementsParserNode(BaseGraphNode):
    """Parses user requirements into structured architecture metadata."""

    def __init__(
        self,
        parser: RequirementsParserService,
        log_manager: ExecutionLogManager,
    ) -> None:
        self._parser = parser
        self._log_manager = log_manager

    @property
    def name(self) -> str:
        return "requirements_parser_node"

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Parse requirements synchronously and record execution log entry."""
        parsed = self._parser.parse(state.get("user_requirement", ""))
        sub_results = dict(state.get("subgraph_results") or {})
        sub_results["architecture_requirements"] = parsed

        logs = self._log_manager.append_to_logs(
            list(state.get("execution_logs") or []),
            agent="architect",
            phase=PHASE_ARCHITECT,
            message=f"Architect: Parsed requirements for domain '{parsed['domain_type']}'.",
            details=parsed,
        )
        return {
            **state,
            "current_node": self.name,
            "subgraph_results": sub_results,
            "execution_logs": logs,
        }

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Execute requirements parsing asynchronously."""
        return self.execute(state)


class BlueprintGeneratorNode(BaseGraphNode):
    """Synthesizes modular system architecture blueprints from state requirements."""

    def __init__(
        self,
        generator: BlueprintGeneratorService,
        log_manager: ExecutionLogManager,
    ) -> None:
        self._generator = generator
        self._log_manager = log_manager

    @property
    def name(self) -> str:
        return "blueprint_generator_node"

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Generate system architecture specification and record execution logs."""
        spec = self._generator.generate(state.get("user_requirement", ""))
        logs = self._log_manager.append_to_logs(
            list(state.get("execution_logs") or []),
            agent="architect",
            phase=PHASE_ARCHITECT,
            message="Architect: Generated architecture specification and module layout.",
            details={"spec_lines": len(spec.splitlines())},
        )
        return {
            **state,
            "architecture_spec": spec,
            "current_node": self.name,
            "execution_logs": logs,
        }

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Execute blueprint synthesis asynchronously."""
        return self.execute(state)


class SchemaValidatorNode(BaseGraphNode):
    """Validates synthesized architecture blueprints and finalizes architecture-only flows."""

    def __init__(
        self,
        validator: BlueprintSchemaValidator,
        log_manager: ExecutionLogManager,
    ) -> None:
        self._validator = validator
        self._log_manager = log_manager

    @property
    def name(self) -> str:
        return "schema_validator_node"

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Validate architecture spec, update completed tasks, and determine completion state."""
        spec = state.get("architecture_spec") or ""
        is_valid = self._validator.validate(spec)

        completed = list(state.get("completed_tasks") or [])
        if is_valid and "architecture" not in completed:
            completed.append("architecture")

        sub_results = dict(state.get("subgraph_results") or {})
        sub_results["architecture_spec_valid"] = is_valid

        logs = self._log_manager.append_to_logs(
            list(state.get("execution_logs") or []),
            agent="architect",
            phase=PHASE_ARCHITECT,
            message="Architect: Validated architecture blueprint schemas and contracts.",
            details={"valid": is_valid},
        )

        next_state: dict[str, Any] = {
            **state,
            "completed_tasks": completed,
            "subgraph_results": sub_results,
            "current_node": self.name,
            "execution_logs": logs,
        }
        if is_valid and self._validator.is_architecture_only(state):
            next_state["status"] = STATUS_COMPLETED
            next_state["current_phase"] = PHASE_COMPLETED
        return next_state

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Execute architecture schema validation asynchronously."""
        return self.execute(state)


__all__ = [
    "BlueprintGeneratorNode",
    "RequirementsParserNode",
    "SchemaValidatorNode",
]
