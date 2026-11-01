"""ToolRetriever & Just-in-Time Tool Provisioning Engine.

Application layer (framework- and I/O-free). Manages per-session active tool sets,
executes the `search_available_tools` meta-tool, dynamically exposes schemas on demand,
and prunes idle tools to maintain token hygiene.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.application.tools.registry import DynamicToolRegistry
from app.domain.tools import ToolDescriptor, ToolRetrievalPolicy

SEARCH_AVAILABLE_TOOLS_NAME = "search_available_tools"

SEARCH_AVAILABLE_TOOLS_DESCRIPTOR = ToolDescriptor(
    name=SEARCH_AVAILABLE_TOOLS_NAME,
    category="meta",
    summary=(
        "Search for relevant tools matching the required operation. "
        "Returns tool summaries and activates matching full schemas for the next step."
    ),
    schema={
        "name": SEARCH_AVAILABLE_TOOLS_NAME,
        "description": (
            "Search for relevant tools matching the required operation. "
            "Returns tool summaries and activates matching full schemas for the next step."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language or keyword query describing the operation "
                        "(e.g. 'read file', 'run tests', 'git diff', 'search web')."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Optional category filter (e.g. 'filesystem', 'terminal', "
                        "'browser', 'code_analysis', 'agent_control')."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    requires_approval=False,
)


class ToolRetriever:
    """Manages active tool sets and executes JIT schema provisioning for an agent turn."""

    def __init__(
        self,
        registry: DynamicToolRegistry,
        policy: ToolRetrievalPolicy | None = None,
        initial_tools: Sequence[str] | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or ToolRetrievalPolicy()
        self._active_tools: dict[str, ToolDescriptor] = {}
        self._tool_last_used: dict[str, int] = {}
        self._current_turn: int = 0

        self._bootstrap_session(initial_tools)

    @property
    def is_tool_less(self) -> bool:
        """Return True if this session operates with 0 tools (pure prompt reasoning)."""
        return self.policy.is_tool_less

    @property
    def max_active_tools(self) -> int:
        return self.policy.max_active_tools

    @property
    def base_tools(self) -> tuple[str, ...]:
        return self.policy.base_tools

    def search_available_tools(
        self,
        query: str,
        category: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search available tool catalog, activate matching schemas, and return compact summaries.

        If the agent is configured as tool-less, returns an empty list without activating tools.
        """
        if self.is_tool_less:
            return []

        search_limit = limit if limit is not None else self.policy.max_active_tools
        matches = self.registry.search(query=query, category=category, limit=search_limit)

        activated_summaries: list[dict[str, Any]] = []
        for desc in matches:
            if desc.name == SEARCH_AVAILABLE_TOOLS_NAME:
                continue

            self._activate(desc)
            activated_summaries.append(
                {
                    "name": desc.name,
                    "category": desc.category,
                    "summary": desc.summary,
                }
            )

        # Enforce active tool budget (evict LRU unpinned tools if needed)
        self._enforce_budget()

        return activated_summaries

    def get_active_tools(self) -> tuple[ToolDescriptor, ...]:
        """Return all currently active tool descriptors in this session."""
        if self.is_tool_less:
            return ()
        return tuple(self._active_tools.values())

    def get_active_tool_names(self) -> tuple[str, ...]:
        """Return names of all currently active tools."""
        if self.is_tool_less:
            return ()
        return tuple(self._active_tools.keys())

    def get_active_schemas(self) -> list[dict[str, Any]]:
        """Return full JSON schema definitions for all active tools to pass to LLM."""
        if self.is_tool_less:
            return []
        schemas: list[dict[str, Any]] = []
        for desc in self._active_tools.values():
            if desc.schema:
                schemas.append(dict(desc.schema))
            else:
                schemas.append(
                    {
                        "name": desc.name,
                        "description": desc.summary,
                        "parameters": {"type": "object", "properties": {}},
                    }
                )
        return schemas

    def activate_tool(self, name: str) -> bool:
        """Manually activate a tool by name if present in registry."""
        if self.is_tool_less:
            return False
        desc = self.registry.get(name)
        if desc is None:
            return False
        self._activate(desc)
        self._enforce_budget()
        return True

    def deactivate_tool(self, name: str) -> bool:
        """Deactivate a tool (cannot deactivate pinned base tools)."""
        if name in self.policy.base_tools:
            return False
        if name in self._active_tools:
            del self._active_tools[name]
            self._tool_last_used.pop(name, None)
            return True
        return False

    def step_turn(
        self,
        used_tool_names: Iterable[str] = (),
        max_idle_turns: int = 3,
    ) -> list[str]:
        """Advance the turn counter, update tool usage timestamps, and prune idle tools.

        Returns list of tool names that were pruned.
        """
        self._current_turn += 1
        for name in used_tool_names:
            if name in self._active_tools:
                self._tool_last_used[name] = self._current_turn

        return self.prune_idle_tools(max_idle_turns=max_idle_turns)

    def prune_idle_tools(self, max_idle_turns: int = 3) -> list[str]:
        """Evict non-base tools that have not been invoked in the last `max_idle_turns` turns."""
        if self.is_tool_less:
            return []

        pruned: list[str] = []
        for name in list(self._active_tools.keys()):
            if name in self.policy.base_tools or name == SEARCH_AVAILABLE_TOOLS_NAME:
                continue

            last_used = self._tool_last_used.get(name, 0)
            idle_turns = self._current_turn - last_used
            if idle_turns >= max_idle_turns:
                del self._active_tools[name]
                self._tool_last_used.pop(name, None)
                pruned.append(name)

        return pruned

    def _bootstrap_session(self, initial_tools: Sequence[str] | None) -> None:
        """Configure initial baseline tools."""
        if self.is_tool_less:
            return

        # 1. Pinned base tools from policy
        for name in self.policy.base_tools:
            if name == SEARCH_AVAILABLE_TOOLS_NAME:
                self._active_tools[name] = SEARCH_AVAILABLE_TOOLS_DESCRIPTOR
                self._tool_last_used[name] = 0
            else:
                desc = self.registry.get(name)
                if desc:
                    self._activate(desc)

        # 2. Add search_available_tools if dynamic retrieval is enabled and not already present
        if (
            self.policy.enabled
            and SEARCH_AVAILABLE_TOOLS_NAME not in self._active_tools
            and self.policy.max_active_tools > len(self._active_tools)
        ):
            self._active_tools[SEARCH_AVAILABLE_TOOLS_NAME] = SEARCH_AVAILABLE_TOOLS_DESCRIPTOR
            self._tool_last_used[SEARCH_AVAILABLE_TOOLS_NAME] = 0

        # 3. Explicit initial tools requested for this session
        if initial_tools:
            for name in initial_tools:
                desc = self.registry.get(name)
                if desc:
                    self._activate(desc)

        self._enforce_budget()

    def _activate(self, desc: ToolDescriptor) -> None:
        """Internal activation of a tool descriptor."""
        self._active_tools[desc.name] = desc
        self._tool_last_used[desc.name] = self._current_turn

    def _enforce_budget(self) -> None:
        """Evict least-recently-used non-pinned tools if active tools exceed limit."""
        if len(self._active_tools) <= self.policy.max_active_tools:
            return

        # Pinned tools that cannot be evicted
        pinned = set(self.policy.base_tools)
        pinned.add(SEARCH_AVAILABLE_TOOLS_NAME)

        # Candidates for eviction
        evictable = [name for name in self._active_tools if name not in pinned]
        # Sort ascending by last used turn (oldest first)
        evictable.sort(key=lambda n: (self._tool_last_used.get(n, 0), n))

        while len(self._active_tools) > self.policy.max_active_tools and evictable:
            victim = evictable.pop(0)
            del self._active_tools[victim]
            self._tool_last_used.pop(victim, None)
