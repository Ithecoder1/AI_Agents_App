"""``HttpStreamingRunner``: template for providers that stream over HTTP + SSE.

A concrete provider only supplies the vendor-specific bits:

* :meth:`build_request`  -- ``ExecutionTask`` -> request (pure mapping, easy to unit test)
* :meth:`parse_chunk`    -- one decoded SSE payload -> ``(THOUGHT|TOKEN, text)`` deltas
* :meth:`auth_headers` / :meth:`health_request` -- authentication and a cheap liveness probe

Everything else is shared: client lifecycle, timeouts, cancellation (closes the live response),
HTTP/transport -> domain error translation, secret redaction, usage + ``DONE`` emission.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from abc import abstractmethod
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from vibe.domain.enums import EventType
from vibe.domain.errors import (
    AuthenticationError,
    ExecutionCancelledError,
    InvalidRequestError,
    StreamDisconnectedError,
    TransientProviderError,
    ValidationError,
)
from vibe.domain.models import (
    AgentProviderConfig,
    ExecutionTask,
    HealthStatus,
    ProviderCapabilities,
    StreamEvent,
    Usage,
)
from vibe.domain.ports.agent_provider import ProviderDeps
from vibe_providers.kit.base import BaseAgentRunner
from vibe_providers.kit.http_errors import (
    error_from_status,
    extract_error_message,
    parse_retry_after,
    translate_httpx_error,
)
from vibe_providers.kit.sse import iter_sse

#: ``(EventType.THOUGHT | EventType.TOKEN, text)``
Delta = tuple[EventType, str]


@dataclass(frozen=True, slots=True)
class HttpRequest:
    path: str
    json: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Mapping[str, str] = field(default_factory=dict)
    method: str = "POST"


@dataclass(slots=True)
class StreamState:
    """Scratch space a ``parse_chunk`` implementation updates while a stream is consumed."""

    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None

    @property
    def has_usage(self) -> bool:
        return bool(self.prompt_tokens or self.completion_tokens or self.cost_usd)

    def to_usage(self) -> Usage:
        return Usage(self.prompt_tokens, self.completion_tokens, self.cost_usd)


class HttpStreamingRunner(BaseAgentRunner):
    default_capabilities = ProviderCapabilities(
        streaming=True, supports_cancel=True, idempotent=True, reports_usage=True
    )

    default_base_url: ClassVar[str]
    default_model: ClassVar[str]
    #: Env var used when the config has no literal/interpolated ``api_key`` option.
    api_key_env: ClassVar[str]
    default_timeout_s: ClassVar[float] = 120.0
    connect_timeout_s: ClassVar[float] = 10.0

    def __init__(
        self,
        config: AgentProviderConfig,
        deps: ProviderDeps,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(config, deps)
        opts = config.options
        self._base_url = str(opts.get("base_url") or self.default_base_url).rstrip("/")
        self._default_model = str(opts.get("default_model") or self.default_model)
        self._timeout_s = float(opts.get("timeout_s") or self.default_timeout_s)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._responses: dict[str, httpx.Response] = {}

    # -- vendor hooks ------------------------------------------------------ #
    @abstractmethod
    def auth_headers(self, api_key: str) -> dict[str, str]: ...

    @abstractmethod
    def build_request(self, task: ExecutionTask, model: str) -> HttpRequest:
        """Map the task to a request. Raise ``ValidationError`` for unusable input."""

    @abstractmethod
    def parse_chunk(
        self, event: str, payload: Mapping[str, Any], state: StreamState
    ) -> Iterable[Delta]:
        """Decode one SSE payload; update *state*; raise ``ProviderError`` on in-stream errors."""

    @abstractmethod
    def health_request(self) -> HttpRequest:
        """A cheap authenticated request (e.g. list models)."""

    # -- plumbing ---------------------------------------------------------- #
    def _api_key(self) -> str | None:
        return str(self.config.options.get("api_key") or "") or self.secret(self.api_key_env)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, transport=self._transport)
        return self._client

    def _redact(self, text: str, api_key: str) -> str:
        return text.replace(api_key, "***") if api_key else text

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def cancel(self, task_id: str) -> None:
        cancel = self._cancel_events.get(task_id)
        if cancel is None:
            return
        cancel.set()
        response = self._responses.get(task_id)
        if response is not None:
            with contextlib.suppress(Exception):
                await response.aclose()

    async def health_check(self) -> HealthStatus:
        api_key = self._api_key()
        if not api_key:
            return HealthStatus.down(f"{self.api_key_env} is not configured")
        request = self.health_request()
        started = time.monotonic()
        try:
            response = await self._get_client().request(
                request.method,
                request.path,
                headers={**self.auth_headers(api_key), **request.headers},
                params=dict(request.params),
                timeout=httpx.Timeout(10.0),
            )
        except Exception as exc:
            return HealthStatus.down(f"unreachable: {type(exc).__name__}")
        if response.is_error:
            return HealthStatus.down(f"HTTP {response.status_code}")
        return HealthStatus.up((time.monotonic() - started) * 1000)

    def _decode(self, data: str) -> Mapping[str, Any] | None:
        if data.strip() == "[DONE]":
            return None
        try:
            payload = json.loads(data)
        except ValueError as exc:
            raise TransientProviderError(
                "malformed stream chunk", provider_id=self.config.id
            ) from exc
        if not isinstance(payload, Mapping):
            raise TransientProviderError("unexpected stream chunk", provider_id=self.config.id)
        return payload

    async def stream_execute(self, task: ExecutionTask) -> AsyncIterator[StreamEvent]:
        provider_id = self.config.id
        api_key = self._api_key()
        if not api_key:
            raise AuthenticationError(
                f"{self.api_key_env} is not configured", provider_id=provider_id
            )

        cancel = asyncio.Event()
        self._cancel_events[task.id] = cancel
        state = StreamState()
        try:
            yield self.event(task, EventType.STATUS, state="started", provider=provider_id)
            try:
                model = task.params.model
                if not model or model.strip().lower() in ("default", "none", "null"):
                    model = self._default_model
                request = self.build_request(task, model)
            except ValidationError as exc:
                raise InvalidRequestError(str(exc), provider_id=provider_id) from exc

            timeout = httpx.Timeout(
                task.params.timeout_s or self._timeout_s, connect=self.connect_timeout_s
            )
            try:
                async with self._get_client().stream(
                    request.method,
                    request.path,
                    headers={**self.auth_headers(api_key), **request.headers},
                    params=dict(request.params),
                    json=dict(request.json),
                    timeout=timeout,
                ) as response:
                    self._responses[task.id] = response
                    if response.is_error:
                        body = await response.aread()
                        raise error_from_status(
                            response.status_code,
                            self._redact(extract_error_message(body), api_key),
                            provider_id=provider_id,
                            retry_after=parse_retry_after(response.headers.get("retry-after")),
                        )
                    async for message in iter_sse(response):
                        if cancel.is_set():
                            raise ExecutionCancelledError("cancelled", provider_id=provider_id)
                        payload = self._decode(message.data)
                        if payload is None:
                            continue
                        for kind, text in self.parse_chunk(message.event, payload, state):
                            yield self.event(task, kind, text=text)
            except (httpx.HTTPError, httpx.StreamError) as exc:
                if cancel.is_set():
                    raise ExecutionCancelledError("cancelled", provider_id=provider_id) from exc
                raise translate_httpx_error(exc, provider_id=provider_id) from exc

            if cancel.is_set():
                raise ExecutionCancelledError("cancelled", provider_id=provider_id)
            if state.finish_reason is None:
                raise StreamDisconnectedError(
                    "stream ended before a finish reason was received", provider_id=provider_id
                )
            if state.has_usage:
                yield self.event(task, EventType.USAGE, **state.to_usage().to_dict())
            yield self.event(task, EventType.DONE, finish_reason=state.finish_reason)
        finally:
            self._cancel_events.pop(task.id, None)
            self._responses.pop(task.id, None)
