"""Core domain models.

All models are frozen dataclasses. ``Mapping`` fields are *treated* as immutable by
convention (don't mutate them in place); use ``dataclasses.replace`` to derive copies.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.domain import prompt_template, state_machine
from app.domain.enums import ExecutionMode, TaskStatus, TaskTrigger
from app.domain.errors import ErrorInfo, ValidationError
from app.domain.events import (
    MetricsEvent,
    StreamEvent,
    TelemetryEvent,
    TextDeltaEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
    telemetry_from_stream_event,
)
from app.domain.providers import (
    AgentProviderConfig,
    HealthStatus,
    ProviderCapabilities,
)
from app.domain.results import Artifact, ExecutionResult
from app.domain.tools import ToolDescriptor, ToolRetrievalPolicy
from app.domain.usage import Usage
from app.domain.workspace import (
    ProjectManifest,
    WorkspaceContext,
)

ParamValue = str | int | float | bool | None


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
CHAT_TURN_ROLES = ("user", "assistant")


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """One prior conversation turn handed to a provider (clean role + final text only)."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in CHAT_TURN_ROLES:
            raise ValidationError(
                f"ChatTurn role must be one of {CHAT_TURN_ROLES}, got {self.role!r}"
            )


@dataclass(frozen=True, slots=True)
class Prompt:
    user_prompt: str
    system_instructions: str = ""
    title: str = ""
    parameters: Mapping[str, ParamValue] = field(default_factory=dict)
    id: str = field(default_factory=new_id)
    version: int = 1
    created_at: datetime = field(default_factory=utcnow)
    # Earlier turns of a multi-turn conversation, oldest first. Strict alternation is not
    # required; adapters normalise for their API. Empty for single-shot executions.
    history: tuple[ChatTurn, ...] = ()
    # True = send text verbatim (chat): ``{{x}}`` in user/system text is not a template variable.
    literal: bool = False

    def variables_used(self) -> set[str]:
        return prompt_template.find_variables(
            self.system_instructions
        ) | prompt_template.find_variables(self.user_prompt)

    def render(self) -> tuple[str, str]:
        """Return ``(system_instructions, user_prompt)`` with variables substituted."""
        if self.literal:
            return self.system_instructions, self.user_prompt
        return (
            prompt_template.render(self.system_instructions, self.parameters),
            prompt_template.render(self.user_prompt, self.parameters),
        )

    def render_messages(self) -> tuple[str, tuple[ChatTurn, ...]]:
        """Return ``(system_instructions, messages)``: ``history`` followed by the current user
        turn. History is always verbatim -- only the current prompt is templated.

        Consecutive turns with the same role (e.g. after a failed earlier turn left a dangling
        user message) are merged, so providers always receive strictly alternating roles.
        """
        system, user = self.render()
        merged: list[ChatTurn] = []
        for turn in (*self.history, ChatTurn("user", user)):
            if merged and merged[-1].role == turn.role:
                merged[-1] = ChatTurn(turn.role, f"{merged[-1].content}\n\n{turn.content}")
            else:
                merged.append(turn)
        return system, tuple(merged)


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
    execution_mode: ExecutionMode = ExecutionMode.DYNAMIC_JIT
    active_tools: Sequence[ToolDescriptor] = ()
    retrieval_policy: ToolRetrievalPolicy | None = None

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
        if not isinstance(self.active_tools, tuple):
            object.__setattr__(self, "active_tools", tuple(self.active_tools))

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

    @property
    def active_tools(self) -> tuple[ToolDescriptor, ...]:
        return tuple(self.params.active_tools)

    @property
    def execution_mode(self) -> ExecutionMode:
        return self.params.execution_mode

    @property
    def retrieval_policy(self) -> ToolRetrievalPolicy | None:
        return self.params.retrieval_policy


__all__ = [
    "CHAT_TURN_ROLES",
    "AgentProviderConfig",
    "Artifact",
    "Attempt",
    "ChatTurn",
    "ExecutionMode",
    "ExecutionParams",
    "ExecutionResult",
    "ExecutionTask",
    "HealthStatus",
    "MetricsEvent",
    "ProjectManifest",
    "Prompt",
    "ProviderCapabilities",
    "StreamEvent",
    "TelemetryEvent",
    "TextDeltaEvent",
    "ThoughtEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "Usage",
    "WorkspaceContext",
    "new_id",
    "telemetry_from_stream_event",
    "utcnow",
]
