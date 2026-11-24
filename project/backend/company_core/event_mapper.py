"""DEPRECATED: Backward-compatibility shim re-exporting ``StartupNodeEventMapper``.

Prefer ``app.infrastructure.orchestration.StartupNodeEventMapper``.
"""

from __future__ import annotations

from app.infrastructure.orchestration.event_mapper import StartupNodeEventMapper

__deprecated__ = True

__all__ = ["StartupNodeEventMapper", "__deprecated__"]
