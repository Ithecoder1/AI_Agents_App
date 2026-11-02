"""File logger observer listener for session trace persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.domain.events import (
    MetricsEvent,
    TelemetryEvent,
    TextDeltaEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class FileLoggerListener:
    """Appends complete structured agent interaction traces to a session log file."""

    def __init__(self, log_file: str | Path) -> None:
        self.log_path = Path(log_file).resolve()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def write_entry(self, entry_type: str, content: str) -> None:
        """Write a timestamped log line to the session log file."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"[{now}] [{entry_type}] {content}\n")

    def __call__(self, event: TelemetryEvent) -> None:
        self.on_event(event)

    def on_event(self, event: TelemetryEvent) -> None:
        """Log strongly-typed TelemetryEvent."""
        if isinstance(event, ThoughtEvent):
            self.write_entry("THOUGHT", event.text)
        elif isinstance(event, ToolCallEvent):
            params_str = ", ".join(f"{k}={v!r}" for k, v in event.args.items())
            self.write_entry("TOOL_CALL", f"{event.name}({params_str})")
        elif isinstance(event, ToolResultEvent):
            self.write_entry("TOOL_RESULT", f"{event.name} -> {event.result}")
        elif isinstance(event, TextDeltaEvent):
            self.write_entry("TEXT_DELTA", event.text)
        elif isinstance(event, MetricsEvent):
            u = event.usage
            self.write_entry(
                "USAGE",
                f"prompt={u.prompt_tokens}, completion={u.completion_tokens}, "
                f"total={u.total_tokens}",
            )
