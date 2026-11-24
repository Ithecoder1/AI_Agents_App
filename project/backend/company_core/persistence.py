"""DEPRECATED: Compatibility shim for persistence and resumability helpers.

Prefer ``app.infrastructure.orchestration.GraphStateHydrator`` and ``JsonStateCheckpointer``.
"""

from __future__ import annotations

__deprecated__ = True

from pathlib import Path

from app.infrastructure.orchestration.persistence import (
    GraphStateHydrator,
    JsonStateCheckpointer,
)
from app.infrastructure.orchestration.state import GraphState

_HYDRATOR = GraphStateHydrator()


def _default_log_path() -> Path:
    return _HYDRATOR.default_log_path()


def save_graph_state(state: GraphState, filepath: Path | str | None = None) -> Path:
    return _HYDRATOR.save(state, filepath)


def save_company_state(state: GraphState, filepath: Path | str | None = None) -> Path:
    return _HYDRATOR.save(state, filepath)


def load_graph_state(filepath: Path | str | None = None) -> GraphState | None:
    return _HYDRATOR.load(filepath)


def load_company_state(filepath: Path | str | None = None) -> GraphState | None:
    return _HYDRATOR.load(filepath)


def can_resume(state: GraphState | None) -> bool:
    return _HYDRATOR.can_resume(state)


def determine_next_incomplete_phase(state: GraphState) -> str:
    return _HYDRATOR.determine_next_incomplete_phase(state)


def hydrate_resumed_state(state: GraphState) -> GraphState:
    return _HYDRATOR.hydrate(state)


__all__ = [
    "GraphStateHydrator",
    "JsonStateCheckpointer",
    "_default_log_path",
    "can_resume",
    "determine_next_incomplete_phase",
    "hydrate_resumed_state",
    "load_company_state",
    "load_graph_state",
    "save_company_state",
    "save_graph_state",
]
