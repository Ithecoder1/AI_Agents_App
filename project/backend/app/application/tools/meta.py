"""Metadata and tool descriptor for the search_available_tools meta-tool."""

from __future__ import annotations

from app.domain.tools import ToolDescriptor

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
