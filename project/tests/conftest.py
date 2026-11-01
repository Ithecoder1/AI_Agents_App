"""Root pytest configuration and shared fixtures: real migrated WAL-tuned SQLite db."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from app.domain.chat import ChatMessage, ChatSession
from app.infrastructure.persistence import (
    SqlAlchemyChatRepository,
    create_engine,
    create_session_factory,
    resolve_database_url,
    upgrade_database,
)
from sqlalchemy.ext.asyncio import AsyncEngine

# Ensure workspace root is discoverable for company_core imports
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(_WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_ROOT))


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    url = resolve_database_url("sqlite+aiosqlite:///chat.db", tmp_path)
    upgrade_database(url)
    eng = create_engine(url)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def repo(engine: AsyncEngine) -> SqlAlchemyChatRepository:
    return SqlAlchemyChatRepository(create_session_factory(engine))


async def make_session(
    repo: SqlAlchemyChatRepository, project_id: str = "proj", title: str = "New chat"
) -> ChatSession:
    return await repo.create_session(
        ChatSession(project_id=project_id, provider_id="fake", model_name="fake-model", title=title)
    )


def draft(session_id: str, role: str = "user", content: str = "hello") -> ChatMessage:
    return ChatMessage(session_id=session_id, role=role, content=content)  # type: ignore[arg-type]
