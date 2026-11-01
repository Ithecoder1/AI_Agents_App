"""Dynamic Tool Registry for Just-in-Time Agent Tool Discovery.

Application layer (framework- and I/O-free). Maintains the catalog of available
workspace and runner tools, providing fast keyword/category indexing and lazy schema exposure.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.domain.tools import ToolDescriptor


class DynamicToolRegistry:
    """In-memory catalog of tool descriptors with keyword and category indexing.

    Supports lazy schema exposure so agents only see concise summaries during discovery,
    deferring full JSON schema injection until a tool is explicitly activated.
    """

    def __init__(self, descriptors: Iterable[ToolDescriptor] | None = None) -> None:
        self._tools: dict[str, ToolDescriptor] = {}
        self._by_category: dict[str, set[str]] = {}
        self._keyword_index: dict[str, set[str]] = {}

        if descriptors:
            self.register_many(descriptors)

    def register(self, descriptor: ToolDescriptor) -> None:
        """Register a single tool descriptor into the catalog and update indices."""
        name = descriptor.name
        self._tools[name] = descriptor

        # Category index
        cat = descriptor.category.lower().strip()
        if cat not in self._by_category:
            self._by_category[cat] = set()
        self._by_category[cat].add(name)

        # Keyword index (tokenized from name, category, and summary)
        tokens = self._tokenize(f"{name} {cat} {descriptor.summary}")
        for token in tokens:
            if token not in self._keyword_index:
                self._keyword_index[token] = set()
            self._keyword_index[token].add(name)

    def register_many(self, descriptors: Iterable[ToolDescriptor]) -> None:
        """Register multiple tool descriptors."""
        for desc in descriptors:
            self.register(desc)

    def get(self, name: str) -> ToolDescriptor | None:
        """Retrieve a descriptor by exact name, or None if not registered."""
        return self._tools.get(name)

    def has_tool(self, name: str) -> bool:
        """Check if a tool exists in the registry."""
        return name in self._tools

    def all_names(self) -> tuple[str, ...]:
        """Return all registered tool names sorted."""
        return tuple(sorted(self._tools.keys()))

    def all_descriptors(self) -> tuple[ToolDescriptor, ...]:
        """Return all registered tool descriptors."""
        return tuple(self._tools.values())

    def categories(self) -> tuple[str, ...]:
        """Return all indexed categories sorted."""
        return tuple(sorted(self._by_category.keys()))

    def search(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> list[ToolDescriptor]:
        """Search registered tools matching query and optional category filter.

        Scoring prioritizes:
        1. Exact tool name matches.
        2. Name contains query.
        3. Token/keyword overlap count in summary and name.
        """
        cleaned_query = query.strip()
        if not cleaned_query and not category:
            # Return top tools up to limit
            return list(self._tools.values())[:limit]

        # Filter by category if requested
        candidate_names: set[str] = set(self._tools.keys())
        if category:
            cat_key = category.lower().strip()
            candidate_names = candidate_names.intersection(self._by_category.get(cat_key, set()))

        if not cleaned_query:
            return [self._tools[n] for n in sorted(candidate_names)[:limit]]

        query_lower = cleaned_query.lower()
        query_tokens = self._tokenize(cleaned_query)

        scored: list[tuple[float, ToolDescriptor]] = []

        # Try compiling regex if query looks like a pattern
        pattern: re.Pattern[str] | None = None
        try:
            pattern = re.compile(cleaned_query, re.IGNORECASE)
        except re.error:
            pattern = None

        for name in candidate_names:
            desc = self._tools[name]
            score = 0.0

            # 1. Exact name match
            if name.lower() == query_lower:
                score += 100.0
            # 2. Name contains query
            elif query_lower in name.lower():
                score += 50.0

            # 3. Regex match on summary
            if pattern and pattern.search(desc.summary):
                score += 25.0

            # 4. Token overlap
            desc_tokens = self._tokenize(f"{desc.name} {desc.category} {desc.summary}")
            common = query_tokens.intersection(desc_tokens)
            score += len(common) * 10.0

            if score > 0.0:
                scored.append((score, desc))

        # Sort descending by score, tie-break by name
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [desc for _, desc in scored[:limit]]

    def list_compact_summaries(self, category: str | None = None) -> list[dict[str, Any]]:
        """Return ultra-lean summaries of tools, avoiding JSON schema overhead."""
        descriptors = (
            [self._tools[n] for n in self._by_category.get(category.lower().strip(), ())]
            if category
            else self._tools.values()
        )
        return [desc.to_compact_summary() for desc in descriptors]

    @classmethod
    def from_catalog_dict(cls, data: Mapping[str, Any]) -> DynamicToolRegistry:
        """Construct registry from parsed JSON/YAML catalog dictionary."""
        registry = cls()
        tools_list = data.get("tools", [])
        if isinstance(tools_list, list):
            for item in tools_list:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    cat = str(item.get("category", "general")).strip()
                    summary = str(item.get("summary", "")).strip()
                    schema = item.get("schema") or {}
                    approval = bool(item.get("requires_approval", False))
                    if name and summary:
                        registry.register(
                            ToolDescriptor(
                                name=name,
                                category=cat,
                                summary=summary,
                                schema=schema if isinstance(schema, dict) else {},
                                requires_approval=approval,
                            )
                        )
        return registry

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Normalize and tokenize text into lowercase alphanumeric keywords."""
        words = re.findall(r"[a-z0-9_-]+", text.lower())
        # Split underscores and dashes as well to capture composite tokens
        tokens: set[str] = set()
        for w in words:
            tokens.add(w)
            parts = re.split(r"[_-]+", w)
            if len(parts) > 1:
                tokens.update(p for p in parts if len(p) > 1)
        return {t for t in tokens if len(t) > 1}
