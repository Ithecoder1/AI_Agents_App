"""Multi-turn chat execution.

``start_turn`` (what ``POST /api/chats/{id}/messages`` calls) persists the user message plus a
``streaming`` assistant placeholder and launches the provider run as a background task, returning
immediately (HTTP 202). The run is decoupled from any WebSocket: events go into a replayable
:class:`TurnStream`, so a client can disconnect, reconnect with a cursor, or never connect at all
and the assistant message is still fully persisted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.application.buffered_writer import BufferedMessageWriter
from app.application.chat_sessions import ChatSessionService
from app.application.conversation_context import ConversationContextBuilder
from app.application.registry import ProviderRegistry
from app.application.stream_aggregator import MessageStreamAggregator
from app.application.workspace_manager import WorkspaceManager
from app.domain.chat import (
    STATUS_CANCELLED,
    STATUS_COMPLETE,
    STATUS_ERROR,
    STATUS_STREAMING,
    ChatMessage,
    ChatSession,
    MessageMetadata,
    MessageUsage,
)
from app.domain.enums import EventType
from app.domain.errors import (
    ConflictError,
    ExecutionCancelledError,
    ProviderError,
    ValidationError,
)
from app.domain.models import ExecutionParams, ExecutionTask, Prompt, StreamEvent
from app.domain.ports.agent_provider import IAgentProvider
from app.domain.ports.chat import ChatRepository

_log = logging.getLogger(__name__)

DEFAULT_MODEL = "default"


class TurnStream:
    """Replayable in-memory event log for one running turn, with live subscription.

    Event ``seq`` values are 1-based and gap-free; ``subscribe(after_seq=N)`` resumes after the
    first ``N`` events (the WebSocket "cursor").
    """

    def __init__(self) -> None:
        self._events: list[StreamEvent] = []
        self._closed = False
        self._cond = asyncio.Condition()

    @property
    def finished(self) -> bool:
        return self._closed

    @property
    def events(self) -> tuple[StreamEvent, ...]:
        return tuple(self._events)

    async def publish(self, event: StreamEvent) -> StreamEvent:
        async with self._cond:
            stamped = event.with_seq(len(self._events) + 1)
            self._events.append(stamped)
            self._cond.notify_all()
        return stamped

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            self._cond.notify_all()

    async def subscribe(self, after_seq: int = 0) -> AsyncIterator[StreamEvent]:
        index = max(after_seq, 0)
        while True:
            async with self._cond:
                curr_idx = index

                def can_progress(idx: int = curr_idx) -> bool:
                    return idx < len(self._events) or self._closed

                await self._cond.wait_for(can_progress)
                batch = self._events[index:]
                finished = self._closed and not batch
            if finished:
                return
            for event in batch:
                index += 1
                yield event


@dataclass(frozen=True, slots=True)
class StartedTurn:
    session: ChatSession
    user_message: ChatMessage
    assistant_message: ChatMessage  # persisted placeholder, status "streaming"
    stream: TurnStream


@dataclass(slots=True)
class _ActiveTurn:
    session_id: str
    message_id: str
    task: ExecutionTask
    provider: IAgentProvider
    stream: TurnStream
    runner: asyncio.Task[None] | None = None


class ChatTurnService:
    def __init__(
        self,
        sessions: ChatSessionService,
        repository: ChatRepository,
        registry: ProviderRegistry,
        workspaces: WorkspaceManager,
        context_builder: ConversationContextBuilder | None = None,
        *,
        flush_interval_s: float = 0.5,
        retain_finished: int = 50,
    ) -> None:
        self._sessions = sessions
        self._repo = repository
        self._registry = registry
        self._workspaces = workspaces
        self._context = context_builder or ConversationContextBuilder()
        self._flush_interval_s = flush_interval_s
        self._retain_finished = retain_finished
        self._busy: set[str] = set()  # session ids with a turn starting or running
        self._active: dict[str, _ActiveTurn] = {}  # by session id
        self._streams: OrderedDict[str, TurnStream] = OrderedDict()  # by assistant message id

    # -- lifecycle --------------------------------------------------------- #
    async def recover_interrupted(self) -> int:
        """At start-up: messages left ``streaming`` by a crash/restart can never complete."""
        return await self._repo.fail_streaming_messages(
            "The server restarted while this response was being generated."
        )

    async def shutdown(self) -> None:
        runners = [t.runner for t in self._active.values() if t.runner is not None]
        for runner in runners:
            runner.cancel()
        await asyncio.gather(*runners, return_exceptions=True)

    # -- queries ----------------------------------------------------------- #
    def get_stream(self, message_id: str) -> TurnStream | None:
        return self._streams.get(message_id)

    def is_running(self, session_id: str) -> bool:
        return session_id in self._busy

    # -- commands ---------------------------------------------------------- #
    async def start_turn(
        self,
        session_id: str,
        user_text: str,
        *,
        system_instructions: str = "",
        provider_id: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> StartedTurn:
        """Persist the user message + assistant placeholder and start the run.

        Raises ``ValidationError`` (empty text / bad params / unknown workspace),
        ``NotFoundError`` (session), ``ConflictError`` (a turn is already running in this
        session), or a provider-resolution error (``ProviderNotFoundError`` /
        ``ProviderUnavailableError``) -- in all those cases nothing is persisted.
        """
        if not user_text.strip():
            raise ValidationError("message content must not be empty")
        if session_id in self._busy:
            raise ConflictError("A response is already being generated in this chat")
        self._busy.add(session_id)  # reserved synchronously: no await between check and add
        try:
            return await self._start(
                session_id,
                user_text,
                system_instructions=system_instructions,
                provider_id=provider_id,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except BaseException:
            self._busy.discard(session_id)
            raise

    async def _start(
        self,
        session_id: str,
        user_text: str,
        *,
        system_instructions: str,
        provider_id: str | None,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> StartedTurn:
        session = await self._sessions.get_session(session_id)
        provider_id = provider_id or session.provider_id
        provider = self._registry.resolve(provider_id)
        workspace = self._workspaces.get_workspace(session.project_id)

        if model:
            model_name = model
        elif provider_id != session.provider_id:
            model_name = DEFAULT_MODEL  # the old model name belongs to another provider
        else:
            model_name = session.model_name

        params = ExecutionParams(
            provider_id=provider_id,
            model=None if model_name == DEFAULT_MODEL else model_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        history = self._context.build_history(session.messages)
        prompt = Prompt(
            user_prompt=user_text,
            system_instructions=self._workspaces.enrich_system_instructions(
                workspace, system_instructions
            ),
            history=history,
            literal=True,
        )
        task = ExecutionTask.create(prompt, params, workspace=str(workspace.root_path)).start(
            provider_id
        )
        context_items = self._workspaces.collect_context_items(workspace)
        run_info = {"provider_id": provider_id, "model": model_name}

        session = await self._sessions.prepare_for_turn(
            session, first_prompt=user_text, provider_id=provider_id, model_name=model_name
        )
        user_draft = ChatMessage(session_id=session.id, role="user", content=user_text)
        assistant_draft = ChatMessage(
            session_id=session.id,
            role="assistant",
            content="",
            metadata=MessageMetadata(
                context_items=context_items,
                extensions={**run_info, "status": STATUS_STREAMING},
            ),
        )
        user_msg, assistant_msg = await self._repo.append_messages(
            session.id, [user_draft, assistant_draft]
        )

        stream = TurnStream()
        active = _ActiveTurn(session.id, assistant_msg.id, task, provider, stream)
        aggregator = MessageStreamAggregator(context_items, run_info)
        writer = BufferedMessageWriter(
            self._repo, assistant_msg.id, aggregator, interval_s=self._flush_interval_s
        )
        self._active[session.id] = active
        self._remember_stream(assistant_msg.id, stream)
        active.runner = asyncio.create_task(
            self._run(active, aggregator, writer), name=f"chat-turn-{assistant_msg.id}"
        )
        return StartedTurn(session, user_msg, assistant_msg, stream)

    async def cancel_turn(self, session_id: str) -> bool:
        """Stop the running turn of *session_id*. ``False`` if nothing is running."""
        active = self._active.get(session_id)
        if active is None:
            return False
        await active.provider.cancel(active.task.id)
        if not active.provider.capabilities.supports_cancel and active.runner is not None:
            active.runner.cancel()
        return True

    # -- execution --------------------------------------------------------- #
    def _remember_stream(self, message_id: str, stream: TurnStream) -> None:
        self._streams[message_id] = stream
        # Evict only finished streams, oldest first; the DB is the source of truth afterwards.
        for key in list(self._streams):
            if len(self._streams) <= self._retain_finished:
                break
            if self._streams[key].finished:
                del self._streams[key]

    async def _run(
        self,
        active: _ActiveTurn,
        aggregator: MessageStreamAggregator,
        writer: BufferedMessageWriter,
    ) -> None:
        started = time.monotonic()
        status = STATUS_COMPLETE
        error: dict[str, Any] | None = None
        try:
            async for event in active.provider.stream_execute(active.task):
                boundary = aggregator.apply(event)
                if event.type is EventType.DONE:
                    continue  # published after the message is durably finalised
                await active.stream.publish(event)
                await writer.after_event(event.type, boundary)
        except (asyncio.CancelledError, ExecutionCancelledError):
            status = STATUS_CANCELLED
        except ProviderError as exc:
            status = STATUS_ERROR
            error = dict(exc.to_info().to_dict())
        except Exception as exc:
            _log.exception("chat turn %s crashed", active.message_id)
            status = STATUS_ERROR
            error = {
                "code": "internal_error",
                "message": str(exc) or type(exc).__name__,
                "retryable": False,
            }
        finally:
            await self._finish(active, aggregator, status, error, started)

    async def _finish(
        self,
        active: _ActiveTurn,
        aggregator: MessageStreamAggregator,
        status: str,
        error: dict[str, Any] | None,
        started: float,
    ) -> None:
        """Persist the final state, roll up telemetry, publish the terminal event. Never raises."""
        try:
            usage = MessageUsage.from_usage(
                aggregator.usage, latency_ms=int((time.monotonic() - started) * 1000)
            )
            metadata = aggregator.metadata(status, **({"error": error} if error else {}))
            terminal: dict[str, Any] = {
                "message_id": active.message_id,
                "status": status,
                "usage": usage.to_dict(),
            }
            try:
                await self._repo.finalize_message(
                    active.message_id,
                    content=aggregator.content,
                    metadata=metadata,
                    usage=usage,
                )
                session = await self._repo.get_session(active.session_id, with_messages=False)
                if session is not None:
                    terminal["session"] = session.to_summary_dict()
            except Exception as exc:
                _log.exception("could not finalise chat message %s", active.message_id)
                status = STATUS_ERROR
                terminal["status"] = status
                error = {
                    "code": "persistence_error",
                    "message": f"Could not save the response: {exc}",
                    "retryable": False,
                }

            if status == STATUS_COMPLETE:
                terminal["finish_reason"] = aggregator.finish_reason or "stop"
                event = StreamEvent.create(
                    active.task.id, active.task.attempt, EventType.DONE, **terminal
                )
            else:
                terminal["code"] = (error or {}).get("code", status)
                terminal["message"] = (error or {}).get("message", "Response was stopped")
                event = StreamEvent.create(
                    active.task.id, active.task.attempt, EventType.ERROR, **terminal
                )
            await active.stream.publish(event)
        finally:
            await active.stream.close()
            self._active.pop(active.session_id, None)
            self._busy.discard(active.session_id)
