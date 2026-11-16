"""``BaseAgentRunner``: template-method base class for provider adapters.

A simple adapter only has to implement :meth:`stream_execute` and :meth:`health_check`.
``execute`` is derived by folding the stream into an :class:`ExecutionResult`.
"""

from __future__ import annotations

import contextlib
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from app.domain.enums import EventType, ExecutionMode, TaskStatus
from app.domain.errors import StreamDisconnectedError
from app.domain.models import (
    AgentProviderConfig,
    Artifact,
    ExecutionResult,
    ExecutionTask,
    HealthStatus,
    ProviderCapabilities,
    StreamEvent,
    TelemetryEvent,
    Usage,
    telemetry_from_stream_event,
)
from app.domain.ports.agent_provider import ProviderDeps
from app.domain.tools import ToolDescriptor, ToolRetrievalPolicy

TelemetryObserver = Callable[[TelemetryEvent], None]


@dataclass(frozen=True, slots=True)
class ResolvedExecutionPlan:
    """Universal execution plan computed during provider pre-flight phase."""

    mode: ExecutionMode
    tools: tuple[ToolDescriptor, ...]
    suppress_tools: bool


async def fold_events(
    task: ExecutionTask,
    provider_id: str,
    events: AsyncIterator[StreamEvent],
    *,
    started_at: float | None = None,
) -> ExecutionResult:
    """Collapse a provider stream into a result.

    Raises :class:`StreamDisconnectedError` if the stream ends without a ``DONE`` event.
    Provider errors propagate untouched.
    """
    t0 = started_at if started_at is not None else time.monotonic()
    text: list[str] = []
    artifacts: list[Artifact] = []
    usage = Usage()
    done = False
    async for event in events:
        if event.type is EventType.TOKEN:
            text.append(str(event.data.get("text", "")))
        elif event.type is EventType.ARTIFACT:
            artifacts.append(Artifact.from_dict(event.data))
        elif event.type is EventType.USAGE:
            usage = usage + Usage.from_dict(event.data)
        elif event.type is EventType.DONE:
            done = True
            break
    if not done:
        raise StreamDisconnectedError(
            "stream ended without a 'done' event", provider_id=provider_id
        )
    return ExecutionResult(
        task_id=task.id,
        status=TaskStatus.SUCCEEDED,
        provider_id=provider_id,
        output_text="".join(text),
        artifacts=tuple(artifacts),
        usage=usage,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


class BaseAgentRunner(ABC):
    """Base class implementing everything in ``IAgentProvider`` that is boilerplate."""

    #: Honest defaults for the adapter; operators may override via config ``capabilities:``.
    default_capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities()

    def __init__(self, config: AgentProviderConfig, deps: ProviderDeps) -> None:
        self.config = config
        self.deps = deps
        self._capabilities = type(self).default_capabilities.merged(config.capabilities)
        self._observers: list[TelemetryObserver] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    # -- telemetry observer pipeline -------------------------------------- #
    def add_observer(self, observer: TelemetryObserver) -> None:
        """Register a callback for telemetry events (ThoughtEvent, ToolCallEvent, etc.)."""
        self._observers.append(observer)

    def remove_observer(self, observer: TelemetryObserver) -> None:
        """Remove a previously registered telemetry observer."""
        with contextlib.suppress(ValueError):
            self._observers.remove(observer)

    def notify_observers(self, event: TelemetryEvent | StreamEvent) -> None:
        """Dispatch a telemetry event to all registered observers."""
        if not self._observers:
            return
        telemetry: TelemetryEvent | None = (
            telemetry_from_stream_event(event) if isinstance(event, StreamEvent) else event
        )
        if telemetry is not None:
            for obs in self._observers:
                obs(telemetry)

    # -- universal pre-flight tool resolution ----------------------------- #
    def resolve_execution_plan(
        self,
        task: ExecutionTask,
        *,
        active_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None = None,
        retrieval_policy: ToolRetrievalPolicy | None = None,
    ) -> ResolvedExecutionPlan:
        """Universal pre-flight phase: enforce capability triage and tool schema pruning.

        If mode is DIRECT_QA or target_tools is empty, suppresses all tool schema definitions.
        """
        mode = task.params.execution_mode
        user_prompt = ""
        with contextlib.suppress(Exception):
            _s, turns = task.prompt_snapshot.render_messages()
            user_prompt = turns[-1].content if turns else ""

        is_direct_qa = (
            mode == ExecutionMode.DIRECT_QA
            or bool(task.params.extra.get("direct_qa"))
            or "direct_qa" in user_prompt.lower()
            or task.prompt_snapshot.system_instructions.startswith("DIRECT_QA:")
            or task.params.extra.get("mode") == "plan"
        )
        if is_direct_qa:
            mode = ExecutionMode.DIRECT_QA

        resolved_tools: tuple[ToolDescriptor, ...] = ()
        if active_tools is not None:
            resolved_tools = tuple(active_tools)
        elif task.params.active_tools:
            resolved_tools = tuple(task.params.active_tools)
        elif task.params.extra.get("tools"):
            raw_tools = task.params.extra.get("tools")
            names: list[str] = (
                [str(t).strip() for t in raw_tools if str(t).strip()]
                if isinstance(raw_tools, list)
                else [t.strip() for t in str(raw_tools).split(",") if t.strip()]
            )
            resolved_tools = tuple(
                ToolDescriptor(name=n, category="custom", summary=f"Tool {n}") for n in names
            )

        policy = retrieval_policy or task.params.retrieval_policy
        if policy and policy.is_tool_less:
            mode = ExecutionMode.DIRECT_QA

        suppress_tools = (mode == ExecutionMode.DIRECT_QA) or (len(resolved_tools) == 0)

        if suppress_tools:
            return ResolvedExecutionPlan(
                mode=mode,
                tools=(),
                suppress_tools=True,
            )

        return ResolvedExecutionPlan(
            mode=mode,
            tools=resolved_tools,
            suppress_tools=False,
        )

    @abstractmethod
    def stream_execute(
        self,
        task: ExecutionTask,
        *,
        active_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None = None,
        retrieval_policy: ToolRetrievalPolicy | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield events (``seq=0``) ending with one ``DONE``; raise ``ProviderError`` on failure.

        Implement as an ``async def`` generator.
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Never raise; report problems as ``HealthStatus.down(reason)``."""
        raise NotImplementedError

    async def execute(
        self,
        task: ExecutionTask,
        *,
        active_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None = None,
        retrieval_policy: ToolRetrievalPolicy | None = None,
    ) -> ExecutionResult:
        return await fold_events(
            task,
            self.config.id,
            self.stream_execute(
                task,
                active_tools=active_tools,
                retrieval_policy=retrieval_policy,
            ),
        )

    async def cancel(self, task_id: str) -> None:
        """Default: nothing to cancel. Override together with ``supports_cancel=True``."""
        return None

    async def approve(self, task_id: str) -> bool:
        """Default: does not support approval. Override with ``supports_approval=True``."""
        return False

    async def aclose(self) -> None:
        """Release clients/processes. Default: nothing to release."""
        return None

    # -- helpers for subclasses ------------------------------------------- #
    def event(self, task: ExecutionTask, type_: EventType, **data: Any) -> StreamEvent:
        evt = StreamEvent.create(task.id, task.attempt, type_, **data)
        self.notify_observers(evt)
        return evt

    def secret(self, name: str) -> str | None:
        return self.deps.secrets.get(name)
