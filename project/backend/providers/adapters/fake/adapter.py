"""Scriptable fake provider.

Options (all optional) -- handy for exercising retries, fallbacks and the UI offline:

``chunks``            list[str]  exact token chunks to stream (default: echo the prompt)
``delay_ms``          int        pause before every chunk
``fail_first_n``      int        the first N *attempts* of each task fail (stateless: keyed on
                                 ``task.attempt``), then succeed
``fail_error``        str        one of: rate_limited, timeout, transient, disconnect,
                                 unavailable, auth, invalid_request, content_rejected,
                                 budget (default: transient)
``fail_after_chunks`` int        stream this many chunks before failing (mid-stream failure)
``retry_after_s``     float      value for ``retry_after`` on failures
``emit_artifact``     bool       also emit a sample unified-diff artifact
``healthy``           bool       what ``health_check`` reports (default true)
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from app.domain.enums import ArtifactKind, EventType
from app.domain.errors import (
    AuthenticationError,
    BudgetExceededError,
    ConfigurationError,
    ContentRejectedError,
    ExecutionCancelledError,
    InvalidRequestError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
    StreamDisconnectedError,
    TransientProviderError,
    ValidationError,
)
from app.domain.models import (
    AgentProviderConfig,
    Artifact,
    ExecutionTask,
    HealthStatus,
    ProviderCapabilities,
    StreamEvent,
    Usage,
)
from app.domain.ports.agent_provider import ProviderDeps

from providers.kit import BaseAgentRunner

_ERRORS: dict[str, type[ProviderError]] = {
    "rate_limited": RateLimitedError,
    "timeout": ProviderTimeoutError,
    "transient": TransientProviderError,
    "disconnect": StreamDisconnectedError,
    "unavailable": ProviderUnavailableError,
    "auth": AuthenticationError,
    "invalid_request": InvalidRequestError,
    "content_rejected": ContentRejectedError,
    "budget": BudgetExceededError,
}

_SAMPLE_DIFF = """\
--- a/hello.py
+++ b/hello.py
@@ -1,2 +1,3 @@
 def greet():
-    print("hello")
+    print("hello from the fake provider")
+    return True
"""


def _split(text: str) -> list[str]:
    words = text.split(" ")
    return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]


class FakeProvider(BaseAgentRunner):
    default_capabilities = ProviderCapabilities(
        streaming=True,
        supports_cancel=True,
        idempotent=True,
        reports_usage=True,
        models=("fake-model",),
    )

    def __init__(self, config: AgentProviderConfig, deps: ProviderDeps) -> None:
        super().__init__(config, deps)
        opts = config.options
        chunks = opts.get("chunks")
        self._chunks: list[str] | None = [str(c) for c in chunks] if chunks else None
        self._delay_s = float(opts.get("delay_ms", 0)) / 1000
        self._fail_first_n = int(opts.get("fail_first_n", 0))
        self._fail_after_chunks = int(opts.get("fail_after_chunks", 0))
        self._retry_after = (
            float(opts["retry_after_s"]) if opts.get("retry_after_s") is not None else None
        )
        self._emit_artifact = bool(opts.get("emit_artifact", False))
        self._healthy = bool(opts.get("healthy", True))
        self._fail_error = str(opts.get("fail_error", "transient"))
        if self._fail_error not in _ERRORS:
            raise ConfigurationError(
                f"fake provider: unknown fail_error {self._fail_error!r}; "
                f"choose one of {sorted(_ERRORS)}"
            )
        self._cancel_events: dict[str, asyncio.Event] = {}

    async def health_check(self) -> HealthStatus:
        if self._healthy:
            return HealthStatus.up(0.0)
        return HealthStatus.down("fake provider configured as unhealthy")

    async def cancel(self, task_id: str) -> None:
        event = self._cancel_events.get(task_id)
        if event is not None:
            event.set()

    async def _pause(self, cancel: asyncio.Event) -> None:
        if self._delay_s > 0:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(cancel.wait(), timeout=self._delay_s)
        else:
            await asyncio.sleep(0)
        if cancel.is_set():
            raise ExecutionCancelledError("cancelled", provider_id=self.config.id)

    def _failure(self, task: ExecutionTask) -> ProviderError:
        return _ERRORS[self._fail_error](
            f"simulated {self._fail_error} (attempt {task.attempt})",
            provider_id=self.config.id,
            retry_after=self._retry_after,
        )

    async def stream_execute(self, task: ExecutionTask) -> AsyncIterator[StreamEvent]:
        cancel = asyncio.Event()
        self._cancel_events[task.id] = cancel
        try:
            yield self.event(task, EventType.STATUS, state="started", provider=self.config.id)
            try:
                _system, user = task.prompt_snapshot.render()
            except ValidationError as exc:
                raise InvalidRequestError(str(exc), provider_id=self.config.id) from exc

            model = task.params.model or "fake-model"
            chunks = self._chunks or _split(f"[fake:{model}] {user}")
            failing = task.attempt <= self._fail_first_n

            yield self.event(task, EventType.THOUGHT, text="Thinking about the request...")
            for index, chunk in enumerate(chunks):
                await self._pause(cancel)
                if failing and index == self._fail_after_chunks:
                    raise self._failure(task)
                yield self.event(task, EventType.TOKEN, text=chunk)
            if failing:
                raise self._failure(task)

            if self._emit_artifact:
                artifact = Artifact(
                    name="changes.diff",
                    kind=ArtifactKind.DIFF,
                    mime="text/x-diff",
                    size=len(_SAMPLE_DIFF),
                    inline_content=_SAMPLE_DIFF,
                )
                yield self.event(task, EventType.ARTIFACT, **artifact.to_dict())

            usage = Usage(
                prompt_tokens=max(1, len(user) // 4),
                completion_tokens=max(1, sum(len(c) for c in chunks) // 4),
                cost_usd=0.0,
            )
            yield self.event(task, EventType.USAGE, **usage.to_dict())
            yield self.event(task, EventType.DONE, finish_reason="stop")
        finally:
            self._cancel_events.pop(task.id, None)
