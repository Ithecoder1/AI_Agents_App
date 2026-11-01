"""Base class and common contract for specialized company agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from company_core.config import AgentPersonaConfig
from company_core.state import CompanyState, append_execution_log


class BaseCompanyAgent(ABC):
    """Abstract Base Class for all specialized startup agent personas."""

    def __init__(
        self,
        persona_name: str,
        config: AgentPersonaConfig | None = None,
    ) -> None:
        self.persona_name = persona_name
        self.config = config

    @property
    def is_tool_less(self) -> bool:
        """Return True if agent has zero tools or tool retrieval is disabled."""
        return self.config.is_tool_less if self.config else True

    @property
    def tools(self) -> tuple[str, ...]:
        """Declared tools for this agent."""
        return self.config.tools if self.config else ()

    @property
    def phase_name(self) -> str:
        """The primary CompanyState phase identifier associated with this agent."""
        raise NotImplementedError

    @abstractmethod
    def execute(self, state: CompanyState) -> CompanyState:
        """Process current state, perform specialized actions, and return updated state."""
        raise NotImplementedError

    def log_event(
        self,
        state: CompanyState,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Helper to append an execution log entry for this agent."""
        existing_logs = state.get("execution_logs") or []
        return append_execution_log(
            existing_logs,
            agent=self.persona_name,
            phase=self.phase_name,
            message=message,
            details=details,
        )
