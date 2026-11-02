"""Stream parsing for Antigravity CLI NDJSON output."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

from app.domain.enums import EventType
from app.domain.models import ExecutionTask, StreamEvent, Usage


class AntigravityStreamParser:
    """Parses real-time NDJSON output lines from `agy -p` subprocess."""

    def __init__(
        self,
        event_factory: Callable[..., StreamEvent],
    ) -> None:
        self._event = event_factory

    async def parse_stream(
        self,
        stdout: asyncio.StreamReader,
        task: ExecutionTask,
    ) -> AsyncIterator[StreamEvent]:
        """Yield parsed domain StreamEvents from the subprocess stdout reader."""
        async for raw_line in stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                yield self._event(task, EventType.LOG, line=line)
                continue

            event_name = payload.get("event")

            if event_name == "init":
                init_meta = payload.get("init", {})
                conv_id = payload.get("conversation_id", "")
                yield self._event(
                    task,
                    EventType.STATUS,
                    state="running",
                    conversation_id=conv_id,
                    tools_count=len(init_meta.get("tools", [])),
                )

            elif event_name == "step_update":
                step = payload.get("step_update", {})
                step_type = step.get("step_type")
                state = step.get("state")

                # Thought / Thinking tokens
                thought = (
                    step.get("thought_delta") or step.get("thinking_delta") or step.get("thought")
                )
                if thought:
                    yield self._event(task, EventType.THOUGHT, text=thought)

                # Tool call / result
                if step_type == "tool":
                    tool_info = step.get("tool_info", {})
                    tool_name = step.get("tool_name") or tool_info.get("name", "tool")
                    if state == "ACTIVE":
                        yield self._event(
                            task,
                            EventType.TOOL_CALL,
                            name=tool_name,
                            args=tool_info.get("parameters", {}),
                        )
                    elif state == "DONE":
                        yield self._event(
                            task,
                            EventType.TOOL_RESULT,
                            name=tool_name,
                            result=tool_info.get("output", ""),
                        )

                # Agent text delta
                text_delta = step.get("text_delta")
                if text_delta:
                    yield self._event(task, EventType.TOKEN, text=text_delta)

            elif event_name == "result":
                res = payload.get("result", {})
                usage_data = res.get("usage", {})
                usage = Usage(
                    prompt_tokens=usage_data.get("input_tokens", 0),
                    completion_tokens=usage_data.get("output_tokens", 0),
                    cost_usd=0.0,
                )
                yield self._event(task, EventType.USAGE, **usage.to_dict())
