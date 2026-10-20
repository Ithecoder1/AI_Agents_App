"""Core domain models.

All models are frozen dataclasses. ``Mapping`` fields are *treated* as immutable by
convention (don't mutate them in place); use ``dataclasses.replace`` to derive copies.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import Any

from vibe.domain import prompt_template, state_machine
from vibe.domain.enums import ArtifactKind, EventType, TaskStatus, TaskTrigger
from vibe.domain.errors import ErrorInfo, ValidationError

ParamValue = str | int | float | bool | None

_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SECRET_KEY_HINTS = ("key", "token", "secret", "password", "credential", "authorization")


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Prompt:
    user_prompt: str
    system_instructions: str = ""
    title: str = ""
    parameters: Mapping[str, ParamValue] = field(default_factory=dict)
    id: str = field(default_factory=new_id)
    version: int = 1
    created_at: datetime = field(default_factory=utcnow)

    def variables_used(self) -> set[str]:
        return prompt_template.find_variables(
            self.system_instructions
        ) | prompt_template.find_variables(self.user_prompt)

    def render(self) -> tuple[str, str]:
        """Return ``(system_instructions, user_prompt)`` with variables substituted."""
        return (
            prompt_template.render(self.system_instructions, self.parameters),
            prompt_template.render(self.user_prompt, self.parameters),
        )


# --------------------------------------------------------------------------- #
# Execution parameters / task
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ExecutionParams:
    provider_id: str
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    budget_usd: float | None = None
    timeout_s: float | None = None
    fallback_provider_ids: tuple[str, ...] = ()
    # Provider-specific passthrough (e.g. OpenRouter routing prefs). Adapters own its schema.
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValidationError("provider_id is required")
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ValidationError("temperature must be between 0 and 2")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValidationError("max_tokens must be positive")
        if self.budget_usd is not None and self.budget_usd < 0:
            raise ValidationError("budget_usd must be >= 0")
        if self.timeout_s is not None and self.timeout_s <= 0:
            raise ValidationError("timeout_s must be positive")

    @property
    def provider_chain(self) -> tuple[str, ...]:
        """Primary provider followed by fallbacks, de-duplicated, order preserved."""
        return tuple(dict.fromkeys((self.provider_id, *self.fallback_provider_ids)))


@dataclass(frozen=True, slots=True)
class Attempt:
    number: int
    provider_id: str
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    error: ErrorInfo | None = None


@dataclass(frozen=True, slots=True)
class ExecutionTask:
    prompt_snapshot: Prompt
    params: ExecutionParams
    id: str = field(default_factory=new_id)
    status: TaskStatus = TaskStatus.QUEUED
    attempt: int = 0  # 1-based number of the current/last attempt; 0 = never started
    attempts: tuple[Attempt, ...] = ()
    workspace: str | None = None
    idempotency_key: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    @classmethod
    def create(
        cls,
        prompt: Prompt,
        params: ExecutionParams,
        *,
        workspace: str | None = None,
        idempotency_key: str | None = None,
    ) -> ExecutionTask:
        return cls(
            prompt_snapshot=prompt,
            params=params,
            workspace=workspace,
            idempotency_key=idempotency_key,
        )

    # -- lifecycle (all return new instances) ------------------------------- #
    def apply(self, trigger: TaskTrigger) -> ExecutionTask:
        return replace(
            self, status=state_machine.transition(self.status, trigger), updated_at=utcnow()
        )

    def start(self, provider_id: str) -> ExecutionTask:
        """QUEUED/RETRYING -> RUNNING and open a new attempt record."""
        moved = self.apply(TaskTrigger.START)
        number = self.attempt + 1
        return replace(
            moved, attempt=number, attempts=(*self.attempts, Attempt(number, provider_id))
        )

    def close_attempt(self, error: ErrorInfo | None = None) -> ExecutionTask:
        if not self.attempts:
            return self
        last = replace(self.attempts[-1], finished_at=utcnow(), error=error)
        return replace(self, attempts=(*self.attempts[:-1], last), updated_at=utcnow())

    @property
    def is_terminal(self) -> bool:
        return state_machine.is_terminal(self.status)


# --------------------------------------------------------------------------- #
# Results / artifacts / usage
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        cost = None
        if self.cost_usd is not None or other.cost_usd is not None:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            cost,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Usage:
        cost = data.get("cost_usd")
        return cls(
            int(data.get("prompt_tokens", 0) or 0),
            int(data.get("completion_tokens", 0) or 0),
            float(cost) if cost is not None else None,
        )


@dataclass(frozen=True, slots=True)
class Artifact:
    name: str
    kind: ArtifactKind = ArtifactKind.FILE
    id: str = field(default_factory=new_id)
    mime: str = "text/plain"
    size: int = 0
    # Small artifacts travel inline; the ArtifactStore moves them to a blob and sets content_ref.
    inline_content: str | None = None
    content_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "mime": self.mime,
            "size": self.size,
            "inline_content": self.inline_content,
            "content_ref": self.content_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Artifact:
        return cls(
            name=str(data["name"]),
            kind=ArtifactKind(data.get("kind", ArtifactKind.FILE.value)),
            id=str(data.get("id") or new_id()),
            mime=str(data.get("mime", "text/plain")),
            size=int(data.get("size", 0) or 0),
            inline_content=data.get("inline_content"),
            content_ref=data.get("content_ref"),
        )


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    task_id: str
    status: TaskStatus
    provider_id: str
    output_text: str = ""
    artifacts: tuple[Artifact, ...] = ()
    usage: Usage = field(default_factory=Usage)
    error: ErrorInfo | None = None
    duration_ms: int = 0


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One observable occurrence during execution.

    Providers emit events with ``seq=0``; the application's event publisher assigns the
    monotonically increasing, persisted ``seq`` used for resumable streaming.

    ``data`` conventions per type:
      token/thought: ``{"text": str}``        status: ``{"state": str, ...}``
      log:           ``{"text": str, "stream": "stdout"|"stderr"}``
      tool_call:     ``{"name": str, "args": dict}``   tool_result: ``{"name": str, "result": Any}``
      artifact:      ``Artifact.to_dict()``   usage: ``Usage.to_dict()``
      error:         ``ErrorInfo.to_dict()``  done: ``{"finish_reason": str}``
    """

    task_id: str
    attempt: int
    type: EventType
    data: Mapping[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: datetime = field(default_factory=utcnow)

    @classmethod
    def create(cls, task_id: str, attempt: int, type: EventType, **data: Any) -> StreamEvent:
        return cls(task_id=task_id, attempt=attempt, type=type, data=data)

    def with_seq(self, seq: int) -> StreamEvent:
        return replace(self, seq=seq)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempt": self.attempt,
            "type": self.type.value,
            "data": dict(self.data),
            "seq": self.seq,
            "ts": self.ts.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> StreamEvent:
        ts = raw.get("ts")
        return cls(
            task_id=str(raw["task_id"]),
            attempt=int(raw.get("attempt", 1)),
            type=EventType(raw["type"]),
            data=dict(raw.get("data") or {}),
            seq=int(raw.get("seq", 0)),
            ts=datetime.fromisoformat(ts) if isinstance(ts, str) else utcnow(),
        )


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    streaming: bool = True
    supports_cancel: bool = False
    # True only if re-running the same task is harmless. Agents that edit files are NOT.
    idempotent: bool = False
    can_modify_workspace: bool = False
    reports_usage: bool = False
    # Can continue a dropped stream instead of restarting from scratch.
    supports_resume: bool = False
    models: tuple[str, ...] = ()

    def merged(self, overrides: Mapping[str, Any]) -> ProviderCapabilities:
        """Apply config overrides, rejecting unknown keys."""
        known = {f.name for f in fields(self)}
        unknown = sorted(set(overrides) - known)
        if unknown:
            raise ValidationError(f"Unknown capability override(s): {', '.join(unknown)}")
        clean = dict(overrides)
        if "models" in clean:
            clean["models"] = tuple(clean["models"])
        return replace(self, **clean)


@dataclass(frozen=True, slots=True)
class HealthStatus:
    ok: bool
    reason: str | None = None
    latency_ms: float | None = None

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def up(cls, latency_ms: float | None = None) -> HealthStatus:
        return cls(True, None, latency_ms)

    @classmethod
    def down(cls, reason: str) -> HealthStatus:
        return cls(False, reason, None)


def _mask(options: Mapping[str, Any]) -> dict[str, Any]:
    masked: dict[str, Any] = {}
    for key, value in options.items():
        if isinstance(value, Mapping):
            masked[key] = _mask(value)
        elif any(hint in key.lower() for hint in _SECRET_KEY_HINTS) and value:
            masked[key] = "***"
        else:
            masked[key] = value
    return masked


@dataclass(frozen=True, slots=True)
class AgentProviderConfig:
    id: str
    kind: str
    display_name: str = ""
    # "package.module:Callable" -- a class or function ``(config, deps) -> IAgentProvider``.
    # If omitted, resolved through the ``vibe.providers`` entry-point group by ``kind``.
    adapter: str | None = None
    enabled: bool = True
    max_concurrency: int = 4
    # repr=False: options may contain resolved secrets. Use ``safe_options()`` for display.
    options: Mapping[str, Any] = field(default_factory=dict, repr=False)
    # Overrides applied on top of the adapter's ``default_capabilities``.
    capabilities: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _PROVIDER_ID.match(self.id):
            raise ValidationError(
                f"Invalid provider id {self.id!r}: use lowercase letters, digits, '-' or '_'"
            )
        if not self.kind:
            raise ValidationError(f"Provider {self.id!r} needs a non-empty 'kind'")
        if self.max_concurrency < 1:
            raise ValidationError(f"Provider {self.id!r}: max_concurrency must be >= 1")

    @property
    def label(self) -> str:
        return self.display_name or self.id

    def safe_options(self) -> dict[str, Any]:
        """Options with secret-looking values masked -- safe for logs and the API."""
        return _mask(self.options)
