"""SQLAlchemy/aiosqlite implementation of :class:`app.domain.ports.chat.ChatRepository`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.chat import (
    STATUS_ERROR,
    STATUS_STREAMING,
    ChatMessage,
    ChatRole,
    ChatSession,
    MessageMetadata,
    MessageUsage,
    SessionTelemetry,
    clean_title,
)
from app.domain.errors import NotFoundError
from app.domain.models import utcnow
from app.infrastructure.persistence.models import ChatMessageRow, ChatSessionRow


def _session_from_row(row: ChatSessionRow, messages: tuple[ChatMessage, ...] = ()) -> ChatSession:
    return ChatSession(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        provider_id=row.provider_id,
        model_name=row.model_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
        telemetry=SessionTelemetry(
            total_input_tokens=row.total_input_tokens,
            total_output_tokens=row.total_output_tokens,
            total_reasoning_tokens=row.total_reasoning_tokens,
            total_tokens=row.total_tokens,
            total_cost_usd=row.total_cost_usd,
            message_count=row.message_count,
            extra=dict(row.telemetry_extra or {}),
        ),
        messages=messages,
    )


def _message_from_row(row: ChatMessageRow) -> ChatMessage:
    return ChatMessage(
        id=row.id,
        session_id=row.session_id,
        seq=row.seq,
        role=cast(ChatRole, row.role),
        content=row.content,
        created_at=row.created_at,
        usage=MessageUsage(
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            reasoning_tokens=row.reasoning_tokens,
            latency_ms=row.latency_ms,
            cost_usd=row.cost_usd,
        ),
        metadata=MessageMetadata.from_dict(row.metadata_json),
    )


def _message_to_row(message: ChatMessage) -> ChatMessageRow:
    return ChatMessageRow(
        id=message.id,
        session_id=message.session_id,
        seq=message.seq,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        reasoning_tokens=message.usage.reasoning_tokens,
        latency_ms=message.usage.latency_ms,
        cost_usd=message.usage.cost_usd,
        metadata_json=message.metadata.to_dict(),
    )


class SqlAlchemyChatRepository:
    """Every public method is one short transaction on its own ``AsyncSession``."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # -- sessions ---------------------------------------------------------- #
    async def create_session(self, session: ChatSession) -> ChatSession:
        t = session.telemetry
        async with self._sf.begin() as db:
            db.add(
                ChatSessionRow(
                    id=session.id,
                    project_id=session.project_id,
                    title=session.title,
                    provider_id=session.provider_id,
                    model_name=session.model_name,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                    total_input_tokens=t.total_input_tokens,
                    total_output_tokens=t.total_output_tokens,
                    total_reasoning_tokens=t.total_reasoning_tokens,
                    total_tokens=t.total_tokens,
                    total_cost_usd=t.total_cost_usd,
                    message_count=t.message_count,
                    telemetry_extra=dict(t.extra),
                )
            )
        return replace(session, messages=())

    async def list_sessions(
        self, project_id: str, *, limit: int = 200, offset: int = 0
    ) -> Sequence[ChatSession]:
        # Lightweight by construction: selects chat_sessions only -- no join to chat_messages,
        # so message bodies and metadata_json are never read for the history list.
        stmt = (
            select(ChatSessionRow)
            .where(ChatSessionRow.project_id == project_id)
            .order_by(ChatSessionRow.updated_at.desc(), ChatSessionRow.id)
            .limit(limit)
            .offset(offset)
        )
        async with self._sf() as db:
            rows = (await db.scalars(stmt)).all()
        return [_session_from_row(r) for r in rows]

    async def get_session(
        self, session_id: str, *, with_messages: bool = True
    ) -> ChatSession | None:
        async with self._sf() as db:
            row = await db.get(ChatSessionRow, session_id)
            if row is None:
                return None
            messages: tuple[ChatMessage, ...] = ()
            if with_messages:
                msg_rows = (
                    await db.scalars(
                        select(ChatMessageRow)
                        .where(ChatMessageRow.session_id == session_id)
                        .order_by(ChatMessageRow.seq)
                    )
                ).all()
                messages = tuple(_message_from_row(m) for m in msg_rows)
            return _session_from_row(row, messages)

    async def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        provider_id: str | None = None,
        model_name: str | None = None,
    ) -> ChatSession | None:
        values: dict[str, Any] = {}
        if title is not None:
            values["title"] = clean_title(title)
        if provider_id is not None:
            values["provider_id"] = provider_id
        if model_name is not None:
            values["model_name"] = model_name
        if values:
            async with self._sf.begin() as db:
                result = cast(
                    "CursorResult[Any]",
                    await db.execute(
                        update(ChatSessionRow)
                        .where(ChatSessionRow.id == session_id)
                        .values(**values)
                    ),
                )
                if result.rowcount == 0:
                    return None
        return await self.get_session(session_id, with_messages=False)

    async def delete_session(self, session_id: str) -> bool:
        # Core DELETE: the database cascades to chat_messages (foreign_keys=ON).
        async with self._sf.begin() as db:
            result = cast(
                "CursorResult[Any]",
                await db.execute(delete(ChatSessionRow).where(ChatSessionRow.id == session_id)),
            )
            return bool(result.rowcount)

    # -- messages ---------------------------------------------------------- #
    async def append_messages(
        self, session_id: str, drafts: Sequence[ChatMessage]
    ) -> list[ChatMessage]:
        if not drafts:
            return []
        async with self._sf.begin() as db:
            # The UPDATE is deliberately the transaction's first statement: it takes SQLite's
            # write lock up front (no read->write upgrade that would fail with "database is
            # locked" under contention), doubles as the existence check, and serialises the
            # MAX(seq) read below against concurrent appenders.
            result = cast(
                "CursorResult[Any]",
                await db.execute(
                    update(ChatSessionRow)
                    .where(ChatSessionRow.id == session_id)
                    .values(
                        message_count=ChatSessionRow.message_count + len(drafts),
                        updated_at=utcnow(),
                    )
                ),
            )
            if result.rowcount == 0:
                raise NotFoundError(f"Chat session {session_id!r} not found")
            last_seq = await db.scalar(
                select(func.coalesce(func.max(ChatMessageRow.seq), 0)).where(
                    ChatMessageRow.session_id == session_id
                )
            )
            persisted = [
                replace(draft, session_id=session_id, seq=int(last_seq or 0) + offset)
                for offset, draft in enumerate(drafts, start=1)
            ]
            db.add_all(_message_to_row(m) for m in persisted)
        return persisted

    async def save_message_progress(
        self, message_id: str, *, content: str, metadata: MessageMetadata
    ) -> None:
        async with self._sf.begin() as db:
            await db.execute(
                update(ChatMessageRow)
                .where(ChatMessageRow.id == message_id)
                .values(content=content, metadata_json=metadata.to_dict())
            )

    async def finalize_message(
        self,
        message_id: str,
        *,
        content: str,
        metadata: MessageMetadata,
        usage: MessageUsage,
    ) -> ChatMessage:
        async with self._sf.begin() as db:
            result = cast(
                "CursorResult[Any]",
                await db.execute(
                    update(ChatMessageRow)
                    .where(ChatMessageRow.id == message_id)
                    .values(
                        content=content,
                        metadata_json=metadata.to_dict(),
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        reasoning_tokens=usage.reasoning_tokens,
                        latency_ms=usage.latency_ms,
                        cost_usd=usage.cost_usd,
                    )
                ),
            )
            if result.rowcount == 0:
                raise NotFoundError(f"Chat message {message_id!r} not found")
            row = await db.get(ChatMessageRow, message_id)
            assert row is not None
            # Atomic roll-up: increments are evaluated by SQLite inside this transaction,
            # so concurrent turns can never lose an update.
            await db.execute(
                update(ChatSessionRow)
                .where(ChatSessionRow.id == row.session_id)
                .values(
                    total_input_tokens=ChatSessionRow.total_input_tokens + usage.input_tokens,
                    total_output_tokens=ChatSessionRow.total_output_tokens + usage.output_tokens,
                    total_reasoning_tokens=ChatSessionRow.total_reasoning_tokens
                    + usage.reasoning_tokens,
                    total_tokens=ChatSessionRow.total_tokens + usage.total_tokens,
                    total_cost_usd=ChatSessionRow.total_cost_usd + usage.cost_usd,
                    updated_at=utcnow(),
                )
            )
            return _message_from_row(row)

    async def fail_streaming_messages(self, reason: str) -> int:
        status = ChatMessageRow.metadata_json["extensions"]["status"].as_string()
        async with self._sf.begin() as db:
            rows = (
                await db.scalars(
                    select(ChatMessageRow).where(
                        ChatMessageRow.role == "assistant", status == STATUS_STREAMING
                    )
                )
            ).all()
            for row in rows:
                meta = MessageMetadata.from_dict(row.metadata_json).with_extensions(
                    status=STATUS_ERROR,
                    error={"code": "interrupted", "message": reason},
                )
                row.metadata_json = meta.to_dict()
            return len(rows)
