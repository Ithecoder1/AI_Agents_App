"""SQLAlchemy/SQLite persistence adapters.

Public surface: :func:`create_engine` (WAL-tuned), :class:`SqlAlchemyChatRepository`,
:func:`upgrade_database` (Alembic). Task/prompt repositories and the event store are not
implemented yet.
"""

from app.infrastructure.persistence.chat_repository import SqlAlchemyChatRepository
from app.infrastructure.persistence.engine import (
    SQLITE_PRAGMAS,
    create_engine,
    create_session_factory,
    resolve_database_url,
)
from app.infrastructure.persistence.migrate import upgrade_database

__all__ = [
    "SQLITE_PRAGMAS",
    "SqlAlchemyChatRepository",
    "create_engine",
    "create_session_factory",
    "resolve_database_url",
    "upgrade_database",
]
