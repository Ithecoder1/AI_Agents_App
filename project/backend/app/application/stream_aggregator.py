"""Fold a provider's ``StreamEvent`` stream into a message's content + rich metadata.

Pure and synchronous: no I/O, so it is trivially unit-testable. The caller decides *when* to
persist; :meth:`MessageStreamAggregator.apply` reports whether the event was a structural
boundary (a good moment to flush to the database).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.domain.chat import (
    STATUS_COMPLETE,
    ContextSnapshot,
    ExecutionPlan,
    LogEntry,
    MessageMetadata,
    ThoughtStep,
    ToolCallRecord,
)
from app.domain.enums import EventType
from app.domain.models import StreamEvent, Usage, new_id, utcnow


class MessageStreamAggregator:
    """Accumulates one assistant turn.

    Boundary rules (``apply`` returns ``True``): ``plan``, ``tool_call``, ``tool_result``,
    ``log``, ``error`` and ``done`` always; ``thought`` only when it opens a *new* thought step
    (so a long reasoning stream is not committed once per delta); ``token`` never.
    """

    def __init__(
        self,
        context_items: tuple[ContextSnapshot, ...] = (),
        extensions: Mapping[str, Any] | None = None,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._clock = clock
        self._context_items = context_items
        self._extensions: dict[str, Any] = dict(extensions or {})
        self._text: list[str] = []
        self._thoughts: list[ThoughtStep] = []
        self._thought_open = False
        self._plan: ExecutionPlan | None = None
        self._tool_calls: list[ToolCallRecord] = []
        self._logs: list[LogEntry] = []
        self._artifacts: list[Mapping[str, Any]] = []
        self.usage = Usage()
        self.finish_reason: str | None = None

    # -- read side --------------------------------------------------------- #
    @property
    def content(self) -> str:
        return "".join(self._text)

    def metadata(self, status: str = STATUS_COMPLETE, **extra: Any) -> MessageMetadata:
        extensions: dict[str, Any] = {**self._extensions, "status": status, **extra}
        if self.finish_reason:
            extensions["finish_reason"] = self.finish_reason
        if self._artifacts:
            extensions["artifacts"] = [dict(a) for a in self._artifacts]
        return MessageMetadata(
            context_items=self._context_items,
            thoughts=tuple(self._thoughts),
            plan=self._plan,
            tool_calls=tuple(self._tool_calls),
            logs=tuple(self._logs),
            extensions=extensions,
        )

    # -- write side -------------------------------------------------------- #
    def apply(self, event: StreamEvent) -> bool:
        data = event.data
        kind = event.type
        if kind is not EventType.THOUGHT:
            self._thought_open = False

        if kind is EventType.TOKEN:
            self._text.append(str(data.get("text", "")))
            return False

        if kind is EventType.THOUGHT:
            return self._apply_thought(data)

        if kind is EventType.PLAN:
            self._plan = ExecutionPlan.from_dict(data)
            return True

        if kind is EventType.TOOL_CALL:
            self._tool_calls.append(
                ToolCallRecord(
                    name=str(data.get("name", "tool")),
                    args=dict(data.get("args") or {}),
                    id=str(data.get("id") or data.get("call_id") or "") or new_id(),
                    started_at=self._clock(),
                )
            )
            return True

        if kind is EventType.TOOL_RESULT:
            self._apply_tool_result(data)
            return True

        if kind is EventType.LOG:
            self._logs.append(
                LogEntry(
                    text=str(data.get("text", "")),
                    stream=str(data.get("stream", "stdout")),
                    level=str(data.get("level", "info")),
                    ts=self._clock(),
                )
            )
            return True

        if kind is EventType.ARTIFACT:
            self._artifacts.append(dict(data))
            return False

        if kind is EventType.USAGE:
            self.usage = self.usage + Usage.from_dict(data)
            return False

        if kind is EventType.DONE:
            self.finish_reason = str(data.get("finish_reason") or "") or None
            return True

        return kind is EventType.ERROR  # STATUS and anything unknown: nothing to persist

    def _apply_thought(self, data: Mapping[str, Any]) -> bool:
        text = str(data.get("text", ""))
        step_kind = str(data.get("kind", "thought"))
        opens_new = not self._thought_open or step_kind != "thought"
        if opens_new:
            self._thoughts.append(
                ThoughtStep(
                    text=text,
                    kind=step_kind,
                    title=str(data.get("title", "")),
                    ts=self._clock(),
                )
            )
            # Only plain reasoning deltas are coalesced; sub-requests/delegations are discrete.
            self._thought_open = step_kind == "thought"
        else:
            last = self._thoughts[-1]
            self._thoughts[-1] = replace(last, text=last.text + text)
        return opens_new

    def _apply_tool_result(self, data: Mapping[str, Any]) -> None:
        call_id = str(data.get("id") or data.get("call_id") or "")
        name = str(data.get("name", "tool"))
        error = data.get("error")
        status = "error" if error else "ok"
        finished = self._clock()
        for index in range(len(self._tool_calls) - 1, -1, -1):
            call = self._tool_calls[index]
            matches = call.id == call_id if call_id else call.name == name
            if call.status == "running" and matches:
                self._tool_calls[index] = replace(
                    call,
                    status=status,
                    result=data.get("result"),
                    error=str(error) if error else None,
                    finished_at=finished,
                )
                return
        # A result with no matching call: still record it rather than dropping data.
        self._tool_calls.append(
            ToolCallRecord(
                name=name,
                status=status,
                result=data.get("result"),
                error=str(error) if error else None,
                started_at=finished,
                finished_at=finished,
            )
        )
