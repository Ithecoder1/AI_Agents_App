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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.application.buffered_writer import BufferedMessageWriter
from app.application.chat_sessions import ChatSessionService
from app.application.conversation_context import ConversationContextBuilder
from app.application.registry import ProviderRegistry
from app.application.stream_aggregator import MessageStreamAggregator
from app.application.tools import DynamicToolRegistry, ToolRetriever
from app.application.triage import (
    LIGHTWEIGHT_SYSTEM_INSTRUCTIONS,
    is_lightweight_query,
)
from app.application.turn_stream import StartedTurn, TurnStream
from app.application.workspace_manager import WorkspaceManager
from app.domain.chat import (
    STATUS_CANCELLED,
    STATUS_COMPLETE,
    STATUS_ERROR,
    STATUS_STREAMING,
    ChatMessage,
    ContextSnapshot,
    MessageMetadata,
    MessageUsage,
)
from app.domain.enums import EventType, ExecutionMode
from app.domain.errors import (
    ConflictError,
    ExecutionCancelledError,
    ProviderError,
    ValidationError,
)
from app.domain.models import ExecutionParams, ExecutionTask, Prompt, StreamEvent
from app.domain.ports.agent_provider import IAgentProvider
from app.domain.ports.chat import ChatRepository
from app.domain.tools import ToolDescriptor

__all__ = ["ChatTurnService", "StartedTurn", "TurnStream"]

_log = logging.getLogger(__name__)

DEFAULT_MODEL = "default"


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
        tool_registry: DynamicToolRegistry | None = None,
        *,
        flush_interval_s: float = 0.5,
        retain_finished: int = 50,
    ) -> None:
        self._sessions = sessions
        self._repo = repository
        self._registry = registry
        self._workspaces = workspaces
        self._tools = tool_registry
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
        tools: Sequence[str] | None = None,
        tool_mode: str | None = None,
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
                tools=tools,
                tool_mode=tool_mode,
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
        tools: Sequence[str] | None = None,
        tool_mode: str | None = None,
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

        is_lightweight = is_lightweight_query(user_text)

        # Resolve active tools, count, and schemas
        active_descs: list[ToolDescriptor] = []
        if not is_lightweight and self._tools is not None:
            if tool_mode == "direct_qa":
                active_descs = []
            elif tools:
                active_descs = [d for t in tools if (d := self._tools.get(t)) is not None]
            else:
                retriever = ToolRetriever(self._tools)
                active_descs = list(retriever.get_active_tools())

        active_tool_names = [t.name for t in active_descs]
        injected_schemas = [
            dict(t.schema) if t.schema else {"name": t.name, "description": t.summary}
            for t in active_descs
        ]

        if is_lightweight:
            enriched_instructions = LIGHTWEIGHT_SYSTEM_INSTRUCTIONS
            context_items: tuple[ContextSnapshot, ...] = ()
        else:
            enriched_instructions = self._workspaces.enrich_system_instructions(
                workspace, system_instructions, query=user_text
            )
            context_items = self._workspaces.collect_context_items(workspace, query=user_text)

        ws_info = {
            "id": workspace.project_id,
            "name": workspace.name,
            "root_path": str(workspace.root_path),
            "runtime": getattr(workspace, "runtime", "python:3.10"),
        }
        prov_info = {
            "id": provider_id,
            "model": model_name,
            "kind": getattr(provider.config, "kind", provider_id),
            "display_name": getattr(provider.config, "display_name", provider_id),
        }
        full_context_payload: dict[str, Any] = {
            "workspace": ws_info,
            "provider": prov_info,
            "system_instructions": enriched_instructions,
            "active_tools": active_tool_names,
            "active_tools_count": len(active_descs),
            "injected_schemas": injected_schemas,
            "context_items": [c.to_dict() for c in context_items],
            "used_files": [],
            "total_duration": None,
            "usage_metrics": {},
            "executed_command": None,
        }
        full_context_payload_text = (
            "=" * 40 + " [FULL CONTEXT PAYLOAD] " + "=" * 40 + "\n"
            f"Workspace: {workspace.name} ({workspace.project_id})\n"
            f"Provider: {provider_id} (model: {model_name})\n"
            f"System Instructions: {enriched_instructions}\n"
            f"Active Tools: {active_tool_names}\n"
            f"Active Tools Count: {len(active_descs)}\n"
            f"Context Items: {[c.label for c in context_items]}\n" + "=" * 100
        )

        turn_telemetry: dict[str, Any] = {
            "system_instructions": enriched_instructions,
            "active_tools": active_tool_names,
            "active_tools_count": len(active_descs),
            "injected_schemas": injected_schemas,
            "full_context_payload": full_context_payload,
            "full_context_payload_text": full_context_payload_text,
            "workspace": ws_info,
        }

        extra_params: dict[str, Any] = {
            "full_context_payload": full_context_payload,
            "full_context_payload_text": full_context_payload_text,
        }
        if is_lightweight:
            extra_params["direct_qa"] = True
            extra_params["mode"] = "plan"

        params = ExecutionParams(
            provider_id=provider_id,
            model=None if model_name == DEFAULT_MODEL else model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            active_tools=tuple(active_descs),
            execution_mode=(
                ExecutionMode.DIRECT_QA
                if (is_lightweight or tool_mode == "direct_qa")
                else ExecutionMode.DYNAMIC_JIT
            ),
            extra=extra_params,
        )
        history = self._context.build_history(session.messages)
        prompt = Prompt(
            user_prompt=user_text,
            system_instructions=enriched_instructions,
            history=history,
            literal=True,
        )
        task = ExecutionTask.create(prompt, params, workspace=str(workspace.root_path)).start(
            provider_id
        )
        run_info = {"provider_id": provider_id, "model": model_name}

        session = await self._sessions.prepare_for_turn(
            session, first_prompt=user_text, provider_id=provider_id, model_name=model_name
        )
        user_draft = ChatMessage(
            session_id=session.id,
            role="user",
            content=user_text,
            metadata=MessageMetadata(extensions=turn_telemetry),
        )
        assistant_draft = ChatMessage(
            session_id=session.id,
            role="assistant",
            content="",
            metadata=MessageMetadata(
                context_items=context_items,
                extensions={**run_info, **turn_telemetry, "status": STATUS_STREAMING},
            ),
        )
        user_msg, assistant_msg = await self._repo.append_messages(
            session.id, [user_draft, assistant_draft]
        )

        stream = TurnStream()
        active = _ActiveTurn(session.id, assistant_msg.id, task, provider, stream)
        aggregator = MessageStreamAggregator(context_items, {**run_info, **turn_telemetry})
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
            duration_s = round(time.monotonic() - started, 2)
            usage = MessageUsage.from_usage(aggregator.usage, latency_ms=int(duration_s * 1000))
            base_meta = aggregator.metadata(status)
            used_files = base_meta.used_files
            executed_command = base_meta.executed_command or base_meta.extensions.get(
                "executed_command"
            )
            usage_metrics = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "thinking_tokens": int(base_meta.extensions.get("thinking_tokens", 0)),
                "cache_read_tokens": int(base_meta.extensions.get("cache_read_tokens", 0)),
                "total_tokens": usage.total_tokens,
            }
            fcp = dict(base_meta.extensions.get("full_context_payload") or {})
            fcp["used_files"] = used_files
            fcp["total_duration"] = duration_s
            fcp["usage_metrics"] = usage_metrics
            if executed_command:
                fcp["executed_command"] = executed_command
            cmd_line = f"Executed Command: {executed_command}\n" if executed_command else ""
            fcp_text = (
                "=" * 40 + " [FULL CONTEXT PAYLOAD] " + "=" * 40 + "\n"
                f"Workspace: {fcp.get('workspace', {}).get('name', '')}\n"
                f"Provider: {fcp.get('provider', {}).get('id', '')}\n"
                f"{cmd_line}"
                f"System Instructions: {base_meta.system_instructions or ''}\n"
                f"Active Tools: {base_meta.active_tools}\n"
                f"Active Tools Count: {base_meta.active_tools_count}\n"
                f"Used Files: {used_files}\n"
                f"Used Files Count: {len(used_files)}\n"
                f"Total Duration: {duration_s}s\n"
                f"Usage Metrics: {usage_metrics}\n" + "=" * 100
            )
            extra_finish: dict[str, Any] = {
                "full_context_payload": fcp,
                "full_context_payload_text": fcp_text,
                "total_duration": duration_s,
                "used_files": used_files,
                "usage_metrics": usage_metrics,
            }
            if executed_command:
                extra_finish["executed_command"] = executed_command
            if error:
                extra_finish["error"] = error
            metadata = aggregator.metadata(status, **extra_finish)
            terminal: dict[str, Any] = {
                "message_id": active.message_id,
                "status": status,
                "usage": usage.to_dict(),
                "full_context_payload": fcp,
                "total_duration": duration_s,
                "used_files": used_files,
                "usage_metrics": usage_metrics,
            }
            if executed_command:
                terminal["executed_command"] = executed_command
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
