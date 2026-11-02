"""Console observer listener for real-time agent telemetry."""

from __future__ import annotations

import sys

from app.domain.events import (
    MetricsEvent,
    TelemetryEvent,
    TextDeltaEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class ConsoleListener:
    """Formats and prints agent telemetry events directly to standard output."""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self._in_thought: bool = False
        self._in_text: bool = False

    def __call__(self, event: TelemetryEvent) -> None:
        self.on_event(event)

    def on_event(self, event: TelemetryEvent) -> None:
        """Handle incoming strongly-typed TelemetryEvent."""
        if isinstance(event, ThoughtEvent):
            if not self._in_thought:
                sys.stdout.write("\n[🧠 Thinking]: ")
                self._in_thought = True
                self._in_text = False
            sys.stdout.write(event.text)
            sys.stdout.flush()

        elif isinstance(event, ToolCallEvent):
            self._close_streaming_blocks()
            params_str = ", ".join(f"{k}={v!r}" for k, v in event.args.items())
            print(f"\n[⚡ Tool Call] 🛠️ {event.name}({params_str})")

        elif isinstance(event, ToolResultEvent):
            self._close_streaming_blocks()
            res_str = str(event.result)
            preview = res_str[:200] + ("..." if len(res_str) > 200 else "")
            print(f"[⚡ Tool Result] ↩️ {event.name}: {preview}")

        elif isinstance(event, TextDeltaEvent):
            if not self._in_text:
                if self._in_thought:
                    sys.stdout.write("\n")
                self._in_text = True
                self._in_thought = False
            sys.stdout.write(event.text)
            sys.stdout.flush()

        elif isinstance(event, MetricsEvent):
            self._close_streaming_blocks()
            u = event.usage
            print(
                f"\n[📊 Usage] prompt: {u.prompt_tokens} tokens | "
                f"completion: {u.completion_tokens} tokens | "
                f"total: {u.total_tokens} tokens"
            )

    def _close_streaming_blocks(self) -> None:
        if self._in_thought or self._in_text:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._in_thought = False
            self._in_text = False
