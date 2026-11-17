"""Node event mapping for Autonomous Startup Orchestrator.

Re-exports StartupNodeEventMapper from company_core.event_mapper for backward compatibility.
"""

from __future__ import annotations

from company_core.event_mapper import StartupNodeEventMapper

__all__ = ["StartupNodeEventMapper"]
