"""Chat session use cases: create / list / get / rename / delete."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from app.domain.chat import ChatSession, clean_title, derive_title
from app.domain.errors import NotFoundError
from app.domain.ports.chat import ChatRepository


class ChatSessionService:
    """Thin orchestration over :class:`ChatRepository`.

    Telemetry roll-ups happen inside the repository's ``finalize_message`` transaction (see the
    port docs), so there is deliberately no read-modify-write of counters in this layer.
    """

    def __init__(self, repository: ChatRepository) -> None:
        self._repo = repository

    async def create_session(
        self,
        project_id: str,
        provider_id: str,
        model_name: str,
        title: str | None = None,
    ) -> ChatSession:
        session_title = clean_title(title) if title and title.strip() else "New chat"
        session = ChatSession(
            project_id=project_id,
            provider_id=provider_id,
            model_name=model_name,
            title=session_title,
        )
        return await self._repo.create_session(session)

    async def list_sessions(
        self, project_id: str, *, limit: int = 200, offset: int = 0
    ) -> Sequence[ChatSession]:
        """Lightweight listing: session columns only, newest activity first."""
        return await self._repo.list_sessions(project_id, limit=limit, offset=offset)

    async def get_session(self, session_id: str, *, with_messages: bool = True) -> ChatSession:
        session = await self._repo.get_session(session_id, with_messages=with_messages)
        if session is None:
            raise NotFoundError(f"Chat session {session_id!r} not found")
        return session

    async def rename_session(self, session_id: str, title: str) -> ChatSession:
        updated = await self._repo.update_session(session_id, title=clean_title(title))
        if updated is None:
            raise NotFoundError(f"Chat session {session_id!r} not found")
        return updated

    async def delete_session(self, session_id: str) -> None:
        if not await self._repo.delete_session(session_id):
            raise NotFoundError(f"Chat session {session_id!r} not found")

    async def prepare_for_turn(
        self,
        session: ChatSession,
        *,
        first_prompt: str,
        provider_id: str,
        model_name: str,
    ) -> ChatSession:
        """Before a new turn: auto-title an untitled, empty chat from its first prompt and keep the
        session's provider/model badge in sync with what is actually about to run.

        A title the user chose is never overwritten.
        """
        title = None
        if session.is_untitled and session.telemetry.message_count == 0:
            title = derive_title(first_prompt)
        new_provider = provider_id if provider_id != session.provider_id else None
        new_model = model_name if model_name != session.model_name else None
        if title is None and new_provider is None and new_model is None:
            return session
        updated = await self._repo.update_session(
            session.id,
            title=title,
            provider_id=new_provider,
            model_name=new_model,
        )
        if updated is None:
            raise NotFoundError(f"Chat session {session.id!r} not found")
        return replace(updated, messages=session.messages)
