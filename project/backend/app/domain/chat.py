"""Chat domain: multi-turn sessions, messages, per-message inspection data and telemetry.

Hybrid model: filterable/sortable facts (ids, role, token counters, cost, timestamps) are
plain typed fields (native columns when persisted); rich, evolving structures live in
:class:`MessageMetadata`, which round-trips through a JSON-safe dict. ``from_dict`` is
deliberately tolerant -- unknown keys are ignored and ``extensions`` is an open dict -- so new
features never need a destructive migration.

Everything is stdlib-only and immutable (frozen dataclasses); derive copies with
``dataclasses.replace``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal, get_args

from app.domain.errors import ValidationError
from app.domain.models import Usage, new_id, utcnow

ChatRole = Literal["system", "user", "assistant", "tool"]
CHAT_ROLES: tuple[str, ...] = get_args(ChatRole)

DEFAULT_SESSION_TITLE = "New chat"
MAX_TITLE_LENGTH = 120

# Lifecycle of an assistant message, stored in ``MessageMetadata.extensions["status"]``.
STATUS_STREAMING = "streaming"
STATUS_COMPLETE = "complete"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"


# --------------------------------------------------------------------------- #
# tolerant coercion helpers (JSON in the database may predate the current schema)
# --------------------------------------------------------------------------- #
def _s(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return utcnow()


def _opt_dt(value: Any) -> datetime | None:
    return None if value is None else _dt(value)


def _items(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [v for v in value if isinstance(v, Mapping)]


# --------------------------------------------------------------------------- #
# message metadata building blocks
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Something attached to a turn: a file, a path, a text snippet or a directory tree."""

    kind: str  # "file" | "directory_tree" | "snippet" | "path"
    label: str
    path: str | None = None
    content: str = ""
    size_bytes: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "path": self.path,
            "content": self.content,
            "size_bytes": self.size_bytes,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ContextSnapshot:
        return cls(
            kind=_s(data.get("kind"), "snippet"),
            label=_s(data.get("label") or data.get("path"), "context"),
            path=_s(data["path"]) if data.get("path") is not None else None,
            content=_s(data.get("content")),
            size_bytes=_i(data.get("size_bytes")),
            truncated=bool(data.get("truncated", False)),
        )


@dataclass(frozen=True, slots=True)
class ThoughtStep:
    """Internal reasoning, a sub-agent delegation, a sub-request or a ReAct step."""

    text: str
    kind: str = "thought"  # "thought" | "delegation" | "sub_request" | "react"
    title: str = ""
    id: str = field(default_factory=new_id)
    ts: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "ts": self.ts.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ThoughtStep:
        return cls(
            text=_s(data.get("text")),
            kind=_s(data.get("kind"), "thought"),
            title=_s(data.get("title")),
            id=_s(data.get("id")) or new_id(),
            ts=_dt(data.get("ts")),
        )


@dataclass(frozen=True, slots=True)
class PlanStep:
    title: str
    status: str = "pending"  # "pending" | "in_progress" | "done" | "skipped" | "failed"
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "status": self.status}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlanStep:
        return cls(
            title=_s(data.get("title")),
            status=_s(data.get("status"), "pending"),
            id=_s(data.get("id")) or new_id(),
        )


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Structured step-by-step plan produced by the agent."""

    steps: tuple[PlanStep, ...] = ()
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "steps": [s.to_dict() for s in self.steps]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionPlan:
        raw_steps: list[Mapping[str, Any]] = []
        for item in data.get("steps") or []:
            # Accept bare strings as well as {"title": ..., "status": ...} objects.
            raw_steps.append(item if isinstance(item, Mapping) else {"title": str(item)})
        return cls(
            steps=tuple(PlanStep.from_dict(s) for s in raw_steps),
            summary=_s(data.get("summary") or data.get("title")),
        )


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """A sub-request, CLI command or tool execution with its input/output and status."""

    name: str
    args: Mapping[str, Any] = field(default_factory=dict)
    status: str = "running"  # "running" | "ok" | "error"
    result: Any = None
    error: str | None = None
    id: str = field(default_factory=new_id)
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "args": dict(self.args),
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ToolCallRecord:
        args = data.get("args")
        return cls(
            name=_s(data.get("name"), "tool"),
            args=dict(args) if isinstance(args, Mapping) else {},
            status=_s(data.get("status"), "ok"),
            result=data.get("result"),
            error=_s(data["error"]) if data.get("error") is not None else None,
            id=_s(data.get("id")) or new_id(),
            started_at=_dt(data.get("started_at")),
            finished_at=_opt_dt(data.get("finished_at")),
        )


@dataclass(frozen=True, slots=True)
class LogEntry:
    text: str
    stream: str = "stdout"  # "stdout" | "stderr"
    level: str = "info"
    ts: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "stream": self.stream,
            "level": self.level,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LogEntry:
        return cls(
            text=_s(data.get("text")),
            stream=_s(data.get("stream"), "stdout"),
            level=_s(data.get("level"), "info"),
            ts=_dt(data.get("ts")),
        )


@dataclass(frozen=True, slots=True)
class MessageMetadata:
    """Everything the UI can inspect about one message turn. Persisted as ``metadata_json``."""

    context_items: tuple[ContextSnapshot, ...] = ()
    thoughts: tuple[ThoughtStep, ...] = ()
    plan: ExecutionPlan | None = None
    tool_calls: tuple[ToolCallRecord, ...] = ()
    logs: tuple[LogEntry, ...] = ()
    # Open dictionary for features that do not exist yet (code diffs, approval gates,
    # citations, ...). Currently used: ``status``, ``finish_reason``, ``error``,
    # ``artifacts``, ``provider_id``, ``model``.
    extensions: Mapping[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return _s(self.extensions.get("status"), STATUS_COMPLETE)

    def with_extensions(self, **values: Any) -> MessageMetadata:
        return replace(self, extensions={**self.extensions, **values})

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_items": [c.to_dict() for c in self.context_items],
            "thoughts": [t.to_dict() for t in self.thoughts],
            "plan": self.plan.to_dict() if self.plan else None,
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "logs": [entry.to_dict() for entry in self.logs],
            "extensions": dict(self.extensions),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> MessageMetadata:
        if not isinstance(data, Mapping):
            return cls()
        plan = data.get("plan")
        extensions = data.get("extensions")
        return cls(
            context_items=tuple(
                ContextSnapshot.from_dict(c) for c in _items(data.get("context_items"))
            ),
            thoughts=tuple(ThoughtStep.from_dict(t) for t in _items(data.get("thoughts"))),
            plan=ExecutionPlan.from_dict(plan) if isinstance(plan, Mapping) else None,
            tool_calls=tuple(ToolCallRecord.from_dict(t) for t in _items(data.get("tool_calls"))),
            logs=tuple(LogEntry.from_dict(entry) for entry in _items(data.get("logs"))),
            extensions=dict(extensions) if isinstance(extensions, Mapping) else {},
        )


# --------------------------------------------------------------------------- #
# usage / telemetry value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class MessageUsage:
    """Usage of one assistant turn. ``reasoning_tokens`` is a subset of ``output_tokens``."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: int | None = None
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_usage(cls, usage: Usage, latency_ms: int | None = None) -> MessageUsage:
        return cls(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            latency_ms=latency_ms,
            cost_usd=usage.cost_usd or 0.0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True, slots=True)
class SessionTelemetry:
    """Roll-up of every turn in a session (maintained atomically by the repository)."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_reasoning_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    message_count: int = 0
    # Extensible session metrics (cache hits, total duration, ...).
    extra: Mapping[str, Any] = field(default_factory=dict)

    def add_turn(self, usage: MessageUsage) -> SessionTelemetry:
        return replace(
            self,
            total_input_tokens=self.total_input_tokens + usage.input_tokens,
            total_output_tokens=self.total_output_tokens + usage.output_tokens,
            total_reasoning_tokens=self.total_reasoning_tokens + usage.reasoning_tokens,
            total_tokens=self.total_tokens + usage.total_tokens,
            total_cost_usd=self.total_cost_usd + usage.cost_usd,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_reasoning_tokens": self.total_reasoning_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "message_count": self.message_count,
            "extra": dict(self.extra),
        }


# --------------------------------------------------------------------------- #
# aggregates
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ChatMessage:
    session_id: str
    role: ChatRole
    content: str
    # Monotonic turn index within the session (1, 2, 3...). 0 = draft, assigned on append.
    seq: int = 0
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)
    usage: MessageUsage = field(default_factory=MessageUsage)
    metadata: MessageMetadata = field(default_factory=MessageMetadata)

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValidationError("session_id is required")
        if self.role not in CHAT_ROLES:
            raise ValidationError(f"role must be one of {CHAT_ROLES}, got {self.role!r}")
        if self.seq < 0:
            raise ValidationError("seq must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "seq": self.seq,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "status": self.metadata.status,
            "usage": self.usage.to_dict(),
            "metadata": self.metadata.to_dict(),
        }


def derive_title(prompt: str) -> str:
    """Session title from the first prompt: first non-empty line, collapsed, truncated."""
    for line in prompt.splitlines():
        text = " ".join(line.split())
        if text:
            if len(text) > 60:
                return text[:59].rstrip() + "…"
            return text
    return DEFAULT_SESSION_TITLE


def clean_title(title: str) -> str:
    text = " ".join(title.split())
    if not text:
        raise ValidationError("title must not be empty")
    if len(text) > MAX_TITLE_LENGTH:
        raise ValidationError(f"title must be at most {MAX_TITLE_LENGTH} characters")
    return text


@dataclass(frozen=True, slots=True)
class ChatSession:
    """A conversation scoped to one workspace (``project_id``).

    ``messages`` is only populated when a single session is loaded in full; list queries
    return sessions with ``messages == ()``.
    """

    project_id: str
    provider_id: str
    model_name: str
    title: str = DEFAULT_SESSION_TITLE
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    telemetry: SessionTelemetry = field(default_factory=SessionTelemetry)
    messages: tuple[ChatMessage, ...] = ()

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValidationError("project_id is required")
        if not self.provider_id:
            raise ValidationError("provider_id is required")
        if not self.model_name:
            raise ValidationError("model_name is required")
        clean_title(self.title)

    @property
    def is_untitled(self) -> bool:
        return self.title == DEFAULT_SESSION_TITLE

    def to_summary_dict(self) -> dict[str, Any]:
        """Lightweight representation used by list endpoints (no messages)."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "provider_id": self.provider_id,
            "model_name": self.model_name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "message_count": self.telemetry.message_count,
            "telemetry": self.telemetry.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        data = self.to_summary_dict()
        data["messages"] = [m.to_dict() for m in self.messages]
        return data
