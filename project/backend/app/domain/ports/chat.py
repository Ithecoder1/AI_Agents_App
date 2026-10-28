"""Persistence port for chat sessions and messages."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.domain.chat import ChatMessage, ChatSession, MessageMetadata, MessageUsage


class ChatRepository(Protocol):
    """Storage contract for the chat aggregates.

    Implementation rules (verified by the persistence tests):

    * ``list_sessions`` is a *lightweight* query: session columns only -- it must never load
      messages or message metadata.
    * ``append_messages`` assigns monotonic ``seq`` values and bumps ``message_count`` /
      ``updated_at`` in the same transaction as the insert.
    * ``save_message_progress`` is a cheap buffered flush (content + metadata only).
    * ``finalize_message`` writes the final message state and rolls its usage into the
      session telemetry *atomically* (increments are done in the database, never as a
      read-modify-write in application code).
    """

    async def create_session(self, session: ChatSession) -> ChatSession: ...

    async def list_sessions(
        self, project_id: str, *, limit: int = 200, offset: int = 0
    ) -> Sequence[ChatSession]: ...

    async def get_session(
        self, session_id: str, *, with_messages: bool = True
    ) -> ChatSession | None: ...

    async def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        provider_id: str | None = None,
        model_name: str | None = None,
    ) -> ChatSession | None:
        """Patch editable session fields. ``None`` if the session does not exist."""
        ...

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session; messages go with it (``ON DELETE CASCADE``)."""
        ...

    async def append_messages(
        self, session_id: str, drafts: Sequence[ChatMessage]
    ) -> list[ChatMessage]:
        """Atomically append *drafts* (all-or-nothing), returning them with ``seq`` assigned."""
        ...

    async def save_message_progress(
        self, message_id: str, *, content: str, metadata: MessageMetadata
    ) -> None: ...

    async def finalize_message(
        self,
        message_id: str,
        *,
        content: str,
        metadata: MessageMetadata,
        usage: MessageUsage,
    ) -> ChatMessage:
        """Persist the final message and roll ``usage`` into its session's telemetry."""
        ...

    async def fail_streaming_messages(self, reason: str) -> int:
        """Crash recovery: mark messages stuck in ``streaming`` as failed. Returns the count."""
        ...
