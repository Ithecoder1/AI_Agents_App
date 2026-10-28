"""Create chat_sessions and chat_messages.

Revision ID: 0001
Revises:
Create Date: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("total_input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "total_reasoning_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("total_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_cost_usd", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("message_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("telemetry_extra", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_sessions")),
    )
    op.create_index(
        "ix_chat_sessions_project_updated",
        "chat_sessions",
        ["project_id", sa.text("updated_at DESC")],
    )
    op.create_index("ix_chat_sessions_total_tokens", "chat_sessions", ["total_tokens"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.CheckConstraint(
            "role IN ('system', 'user', 'assistant', 'tool')",
            name=op.f("ck_chat_messages_role"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name=op.f("fk_chat_messages_session_id_chat_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
    )
    op.create_index(
        "ux_chat_messages_session_seq", "chat_messages", ["session_id", "seq"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ux_chat_messages_session_seq", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_total_tokens", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_project_updated", table_name="chat_sessions")
    op.drop_table("chat_sessions")
