"""Domain events package.

Encapsulates individual event models following strict one-class-per-file layout.
"""

from __future__ import annotations

from app.domain.events.mapper import TelemetryEvent, telemetry_from_stream_event
from app.domain.events.metrics_event import MetricsEvent
from app.domain.events.stream_event import StreamEvent
from app.domain.events.text_delta_event import TextDeltaEvent
from app.domain.events.thought_event import ThoughtEvent
from app.domain.events.tool_call_event import ToolCallEvent
from app.domain.events.tool_result_event import ToolResultEvent

__all__ = [
    "MetricsEvent",
    "StreamEvent",
    "TelemetryEvent",
    "TextDeltaEvent",
    "ThoughtEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "telemetry_from_stream_event",
]
