"""Telemetry event mapper and union type."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.enums import EventType
from app.domain.events.metrics_event import MetricsEvent
from app.domain.events.stream_event import StreamEvent
from app.domain.events.text_delta_event import TextDeltaEvent
from app.domain.events.thought_event import ThoughtEvent
from app.domain.events.tool_call_event import ToolCallEvent
from app.domain.events.tool_result_event import ToolResultEvent
from app.domain.usage import Usage

TelemetryEvent = ThoughtEvent | ToolCallEvent | ToolResultEvent | TextDeltaEvent | MetricsEvent


def telemetry_from_stream_event(event: StreamEvent) -> TelemetryEvent | None:
    """Map a StreamEvent to a domain TelemetryEvent, or return None if unmapped."""
    if event.type is EventType.THOUGHT:
        return ThoughtEvent(
            text=str(event.data.get("text", "")),
            task_id=event.task_id,
            attempt=event.attempt,
            ts=event.ts,
        )
    if event.type is EventType.TOOL_CALL:
        raw_args = event.data.get("args")
        tool_args: Mapping[str, Any] = raw_args if isinstance(raw_args, Mapping) else {}
        return ToolCallEvent(
            name=str(event.data.get("name", "")),
            args=tool_args,
            task_id=event.task_id,
            attempt=event.attempt,
            ts=event.ts,
        )
    if event.type is EventType.TOOL_RESULT:
        return ToolResultEvent(
            name=str(event.data.get("name", "")),
            result=event.data.get("result", ""),
            task_id=event.task_id,
            attempt=event.attempt,
            ts=event.ts,
        )
    if event.type is EventType.TOKEN:
        return TextDeltaEvent(
            text=str(event.data.get("text", "")),
            task_id=event.task_id,
            attempt=event.attempt,
            ts=event.ts,
        )
    if event.type is EventType.USAGE:
        return MetricsEvent(
            usage=Usage.from_dict(event.data),
            task_id=event.task_id,
            attempt=event.attempt,
            ts=event.ts,
        )
    return None
