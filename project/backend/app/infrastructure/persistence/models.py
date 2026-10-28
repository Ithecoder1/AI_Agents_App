"""SQLAlchemy 2.0 table definitions for chat sessions and messages (hybrid relational + JSON).

Filterable / sortable facts are native, indexed columns; rich per-message inspection data lives
in the validated ``metadata_json`` column (see ``app.domain.chat.MessageMetadata``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    TypeDecorator,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names keep Alembic autogenerate / diffs stable.
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


# Alembic cannot reflect/compare expression indexes (``updated_at DESC``) on SQLite: it would
# propose dropping and re-creating them forever. They are maintained by hand in the migrations
# and verified by tests/persistence instead.
ALEMBIC_IGNORED_INDEXES = frozenset({"ix_chat_sessions_project_updated"})


def alembic_include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    return not (type_ == "index" and name in ALEMBIC_IGNORED_INDEXES)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on SQLite (which has no native tz support).

    Stored as naive UTC (sortable ISO text); always returned tz-aware.
    """

    impl = TIMESTAMP
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=timezone.utc)


class ChatSessionRow(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # References a workspace by id. Workspaces are registered at runtime (not stored in this
    # database), so this is an indexed reference rather than a database foreign key.
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    provider_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    total_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_reasoning_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_cost_usd: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0.0")
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    telemetry_extra: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )

    __table_args__ = (
        # Also serves ``WHERE project_id = ?`` (leading column).
        Index("ix_chat_sessions_project_updated", "project_id", text("updated_at DESC")),
        Index("ix_chat_sessions_total_tokens", "total_tokens"),
    )


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Subset of ``output_tokens``; needed so the session roll-up can break output down.
    reasoning_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0.0")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )

    __table_args__ = (
        Index("ux_chat_messages_session_seq", "session_id", "seq", unique=True),
        CheckConstraint("role IN ('system', 'user', 'assistant', 'tool')", name="role"),
    )
