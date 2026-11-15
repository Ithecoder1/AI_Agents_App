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
        stdout: asyncio.StreamReader | AsyncIterator[bytes],
        task: ExecutionTask,
    ) -> AsyncIterator[StreamEvent]:
        seen_result_usage = False
        fallback_usage: Usage | None = None
        async for raw_line in stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                yield self._event(
                    task,
                    EventType.LOG,
                    line=line,
                    text=line,
                    stream="stdout",
                    raw_payload={"raw": line},
                )
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
                    raw_payload=payload,
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
                    yield self._event(
                        task,
                        EventType.THOUGHT,
                        text=thought,
                        raw_payload=payload,
                    )

                # Tool call / result
                if step_type == "tool":
                    tool_info = step.get("tool_info", {})
                    tool_name = step.get("tool_name") or tool_info.get("name", "tool")
                    if state == "ACTIVE":
                        p_params = tool_info.get("parameters", {})
                        p_str = (
                            ", ".join(f"{k}={v!r}" for k, v in p_params.items())
                            if isinstance(p_params, dict)
                            else str(p_params)
                        )
                        yield self._event(
                            task,
                            EventType.LOG,
                            text=f"[Tool: {tool_name}] {p_str}",
                            stream="stdout",
                            level="info",
                            raw_payload=payload,
                        )
                        yield self._event(
                            task,
                            EventType.TOOL_CALL,
                            name=tool_name,
                            args=p_params if isinstance(p_params, dict) else {},
                            raw_payload=payload,
                        )
                    elif state == "DONE":
                        out = tool_info.get("output", "")
                        preview = str(out).strip().replace("\n", " ")
                        if len(preview) > 120:
                            preview = preview[:117] + "..."
                        yield self._event(
                            task,
                            EventType.LOG,
                            text=f"[Tool Result] {preview}",
                            stream="stdout",
                            level="info",
                            raw_payload=payload,
                        )
                        yield self._event(
                            task,
                            EventType.TOOL_RESULT,
                            name=tool_name,
                            result=out,
                            raw_payload=payload,
                        )

                # Record fallback step usage in case no result event arrives
                step_usage = step.get("usage")
                if isinstance(step_usage, dict):
                    fallback_usage = Usage(
                        prompt_tokens=int(step_usage.get("input_tokens", 0)),
                        completion_tokens=int(step_usage.get("output_tokens", 0)),
                        reasoning_tokens=int(step_usage.get("thinking_tokens", 0)),
                        cost_usd=0.0,
                    )

                # Agent text delta
                text_delta = step.get("text_delta")
                if text_delta:
                    yield self._event(
                        task,
                        EventType.TOKEN,
                        text=text_delta,
                        raw_payload=payload,
                    )

            elif event_name == "result":
                res = payload.get("result", {})
                usage_data = res.get("usage", {})
                if isinstance(usage_data, dict):
                    usage = Usage(
                        prompt_tokens=int(usage_data.get("input_tokens", 0)),
                        completion_tokens=int(usage_data.get("output_tokens", 0)),
                        reasoning_tokens=int(usage_data.get("thinking_tokens", 0)),
                        cost_usd=0.0,
                    )
                    seen_result_usage = True
                    yield self._event(
                        task,
                        EventType.USAGE,
                        **usage.to_dict(),
                        raw_payload=payload,
                    )

        if not seen_result_usage and fallback_usage is not None:
            yield self._event(
                task,
                EventType.USAGE,
                **fallback_usage.to_dict(),
            )
