"""Dynamic Tool Retrieval & Just-in-Time Tool Provisioning Domain Contracts.

Pure domain layer (stdlib only). Defines immutable tool descriptors and retrieval
policies without coupling to concrete tool execution engines or frameworks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import ExecutionMode
from app.domain.errors import ValidationError

__all__ = ["ExecutionMode", "ToolDescriptor", "ToolRetrievalPolicy"]


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Immutable domain specification of an agent tool.

    Attributes:
        name: Unique tool name (e.g. 'read_file').
        category: Logical grouping (e.g. 'filesystem', 'terminal', 'vcs', 'code_analysis').
        summary: 1-sentence concise description exposed during tool discovery.
        schema: Complete JSON Schema defining parameter shapes (loaded lazily on demand).
        requires_approval: Flag indicating if tool execution requires explicit authorization.
    """

    name: str
    category: str
    summary: str
    schema: Mapping[str, Any] = field(default_factory=dict)
    requires_approval: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("ToolDescriptor 'name' cannot be empty")
        if not self.summary or not self.summary.strip():
            raise ValidationError("ToolDescriptor 'summary' cannot be empty")

    def to_compact_summary(self) -> dict[str, Any]:
        """Return ultra-lean discovery representation (minimal token footprint)."""
        return {
            "name": self.name,
            "category": self.category,
            "summary": self.summary,
            "requires_approval": self.requires_approval,
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert descriptor to a standard dictionary representation."""
        return {
            "name": self.name,
            "category": self.category,
            "summary": self.summary,
            "schema": dict(self.schema),
            "requires_approval": self.requires_approval,
        }


@dataclass(frozen=True, slots=True)
class ToolRetrievalPolicy:
    """Configuration governing Just-in-Time tool provisioning for an agent or session.

    Attributes:
        enabled: Whether dynamic tool retrieval is active.
        max_active_tools: Upper bound on active tool schemas injected per model turn.
        base_tools: Core pinned tool names always injected upfront (empty for 0-tool agents).
    """

    enabled: bool = True
    max_active_tools: int = 5
    base_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_active_tools < 0:
            raise ValidationError("ToolRetrievalPolicy 'max_active_tools' cannot be negative")

    @property
    def is_tool_less(self) -> bool:
        """Return True if the policy specifies an agent that must never have tools injected."""
        return not self.enabled or (self.max_active_tools == 0 and len(self.base_tools) == 0)

    @classmethod
    def tool_less(cls) -> ToolRetrievalPolicy:
        """Convenience factory for pure prompt/text-reasoning agents (0 tools)."""
        return cls(enabled=False, max_active_tools=0, base_tools=())

    @classmethod
    def lean_dynamic(
        cls,
        base_tools: tuple[str, ...] = ("search_available_tools",),
        max_active_tools: int = 5,
    ) -> ToolRetrievalPolicy:
        """Convenience factory for dynamic JIT tool provisioning with minimal initial baseline."""
        return cls(enabled=True, max_active_tools=max_active_tools, base_tools=base_tools)
