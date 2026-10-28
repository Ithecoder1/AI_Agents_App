from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.domain.chat import (
    STATUS_ERROR,
    STATUS_STREAMING,
    ChatMessage,
    MessageMetadata,
    MessageUsage,
    ToolCallRecord,
)
from app.domain.errors import NotFoundError
from app.domain.models import utcnow
from app.infrastructure.persistence import (
    SQLITE_PRAGMAS,
    SqlAlchemyChatRepository,
    create_engine,
    resolve_database_url,
    upgrade_database,
)
from app.infrastructure.persistence.models import Base, alembic_include_object
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.persistence.conftest import draft, make_session


# --------------------------------------------------------------------------- #
# engine tuning + schema
# --------------------------------------------------------------------------- #
async def test_pragmas_applied_on_every_connection(engine: AsyncEngine) -> None:
    assert len(SQLITE_PRAGMAS) == 4
    async with engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA journal_mode"))).scalar() == "wal"
        assert (await conn.execute(text("PRAGMA synchronous"))).scalar() == 1  # NORMAL
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1
        assert (await conn.execute(text("PRAGMA busy_timeout"))).scalar() == 5000


def test_migration_matches_models_and_defines_required_indexes(tmp_path: Path) -> None:
    url = resolve_database_url("sqlite+aiosqlite:///m.db", tmp_path)
    upgrade_database(url)
    upgrade_database(url)  # idempotent
    sync = create_sync_engine(url.set(drivername="sqlite"))
    try:
        with sync.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"include_object": alembic_include_object, "compare_type": True}
            )
            diff = compare_metadata(ctx, Base.metadata)
            assert diff == [], diff

            insp = inspect(conn)
            session_ix = {i["name"]: i for i in insp.get_indexes("chat_sessions")}
            assert session_ix["ix_chat_sessions_project_updated"]["column_names"][0] == "project_id"
            assert "ix_chat_sessions_total_tokens" in session_ix
            msg_ix = {i["name"]: i for i in insp.get_indexes("chat_messages")}
            assert msg_ix["ux_chat_messages_session_seq"]["unique"]
            assert msg_ix["ux_chat_messages_session_seq"]["column_names"] == ["session_id", "seq"]

            ddl = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name='ix_chat_sessions_project_updated'")
            ).scalar()
            assert ddl is not None
            assert "updated_at DESC" in ddl
    finally:
        sync.dispose()


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
async def test_list_sessions_scoped_sorted_and_lightweight(
    repo: SqlAlchemyChatRepository, engine: AsyncEngine
) -> None:
    old = await make_session(repo, "alpha", "old")
    new = await make_session(repo, "alpha", "new")
    await make_session(repo, "beta", "other project")
    await repo.append_messages(old.id, [draft(old.id)])  # touching `old` makes it most recent

    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    sessions = await repo.list_sessions("alpha")

    assert [s.id for s in sessions] == [old.id, new.id]
    assert all(s.messages == () for s in sessions)
    assert sessions[0].telemetry.message_count == 1
    assert statements, "list query did not run"
    assert not any("chat_messages" in s for s in statements), statements
    assert not any("metadata_json" in s for s in statements), statements


async def test_get_session_loads_ordered_messages(repo: SqlAlchemyChatRepository) -> None:
    s = await make_session(repo)
    await repo.append_messages(s.id, [draft(s.id, "user", "q1"), draft(s.id, "assistant", "a1")])
    await repo.append_messages(s.id, [draft(s.id, "user", "q2")])

    full = await repo.get_session(s.id)
    assert full is not None
    assert [(m.seq, m.content) for m in full.messages] == [(1, "q1"), (2, "a1"), (3, "q2")]
    assert full.telemetry.message_count == 3
    assert full.created_at.tzinfo is not None

    light = await repo.get_session(s.id, with_messages=False)
    assert light is not None and light.messages == ()
    assert await repo.get_session("missing") is None


async def test_update_session_and_missing(repo: SqlAlchemyChatRepository) -> None:
    s = await make_session(repo)
    updated = await repo.update_session(s.id, title="  Renamed   chat ", model_name="m2")
    assert updated is not None
    assert (updated.title, updated.model_name, updated.provider_id) == (
        "Renamed chat",
        "m2",
        "fake",
    )
    assert await repo.update_session("missing", title="x") is None


async def test_delete_cascades_to_messages(
    repo: SqlAlchemyChatRepository, engine: AsyncEngine
) -> None:
    s = await make_session(repo)
    keep = await make_session(repo, title="keep")
    await repo.append_messages(s.id, [draft(s.id), draft(s.id, "assistant", "a")])
    await repo.append_messages(keep.id, [draft(keep.id)])

    assert await repo.delete_session(s.id) is True
    assert await repo.delete_session(s.id) is False

    async with engine.connect() as conn:
        left = (await conn.execute(text("SELECT session_id FROM chat_messages"))).scalars().all()
    assert left == [keep.id]


# --------------------------------------------------------------------------- #
# messages: seq + atomic telemetry
# --------------------------------------------------------------------------- #
async def test_append_to_unknown_session_raises_and_writes_nothing(
    repo: SqlAlchemyChatRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await repo.append_messages("missing", [draft("missing")])


async def test_concurrent_appends_get_unique_monotonic_seq(
    repo: SqlAlchemyChatRepository,
) -> None:
    s = await make_session(repo)
    results = await asyncio.gather(
        *(repo.append_messages(s.id, [draft(s.id, content=f"m{i}")]) for i in range(25))
    )
    seqs = sorted(m.seq for batch in results for m in batch)
    assert seqs == list(range(1, 26))
    full = await repo.get_session(s.id)
    assert full is not None and full.telemetry.message_count == 25


async def test_unique_seq_index_is_enforced(
    repo: SqlAlchemyChatRepository, engine: AsyncEngine
) -> None:
    s = await make_session(repo)
    await repo.append_messages(s.id, [draft(s.id)])
    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO chat_messages (id, session_id, seq, role, content, created_at) "
                    "VALUES ('dup', :sid, 1, 'user', 'x', '2026-01-01 00:00:00')"
                ),
                {"sid": s.id},
            )


async def test_role_check_constraint(repo: SqlAlchemyChatRepository, engine: AsyncEngine) -> None:
    s = await make_session(repo)
    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO chat_messages (id, session_id, seq, role, content, created_at) "
                    "VALUES ('r', :sid, 1, 'wizard', 'x', '2026-01-01 00:00:00')"
                ),
                {"sid": s.id},
            )


async def test_finalize_rolls_up_usage_and_persists_metadata(
    repo: SqlAlchemyChatRepository,
) -> None:
    s = await make_session(repo)
    placeholder = ChatMessage(
        session_id=s.id,
        role="assistant",
        content="",
        metadata=MessageMetadata().with_extensions(status=STATUS_STREAMING),
    )
    (user, assistant) = await repo.append_messages(s.id, [draft(s.id), placeholder])

    meta = MessageMetadata(
        tool_calls=(ToolCallRecord(name="ls", args={"path": "."}, status="ok", result="a\nb"),)
    ).with_extensions(status="complete", note={"nested": [1, 2]})
    done = await repo.finalize_message(
        assistant.id,
        content="final answer",
        metadata=meta,
        usage=MessageUsage(
            input_tokens=100, output_tokens=40, reasoning_tokens=10, latency_ms=1200, cost_usd=0.01
        ),
    )
    assert done.usage.latency_ms == 1200

    full = await repo.get_session(s.id)
    assert full is not None
    t = full.telemetry
    assert (t.total_input_tokens, t.total_output_tokens, t.total_reasoning_tokens) == (100, 40, 10)
    assert t.total_tokens == 140
    assert t.total_cost_usd == pytest.approx(0.01)
    assert t.message_count == 2
    stored = full.messages[1]
    assert stored.content == "final answer"
    assert stored.metadata.tool_calls[0].result == "a\nb"
    assert stored.metadata.extensions["note"] == {"nested": [1, 2]}
    assert user.seq == 1 and assistant.seq == 2


async def test_concurrent_finalizations_never_lose_rollup_updates(
    repo: SqlAlchemyChatRepository,
) -> None:
    s = await make_session(repo)
    created = [
        (await repo.append_messages(s.id, [draft(s.id, "assistant", "")]))[0] for _ in range(20)
    ]
    await asyncio.gather(
        *(
            repo.finalize_message(
                m.id,
                content="x",
                metadata=MessageMetadata(),
                usage=MessageUsage(
                    input_tokens=3, output_tokens=2, reasoning_tokens=1, cost_usd=0.5
                ),
            )
            for m in created
        )
    )
    full = await repo.get_session(s.id, with_messages=False)
    assert full is not None
    t = full.telemetry
    assert (t.total_input_tokens, t.total_output_tokens, t.total_reasoning_tokens) == (60, 40, 20)
    assert t.total_tokens == 100
    assert t.total_cost_usd == pytest.approx(10.0)


async def test_writes_proceed_while_history_is_being_read(
    repo: SqlAlchemyChatRepository,
) -> None:
    """WAL: a stream of progress flushes must not collide with concurrent list/get reads."""
    s = await make_session(repo)
    (assistant,) = await repo.append_messages(s.id, [draft(s.id, "assistant", "")])

    async def writer() -> None:
        for i in range(40):
            await repo.save_message_progress(
                assistant.id, content="tok " * i, metadata=MessageMetadata()
            )

    async def reader() -> None:
        for _ in range(40):
            await repo.list_sessions("proj")
            await repo.get_session(s.id)

    await asyncio.gather(writer(), reader(), reader())
    full = await repo.get_session(s.id)
    assert full is not None and full.messages[0].content == "tok " * 39


async def test_fail_streaming_messages_marks_only_streaming_ones(
    repo: SqlAlchemyChatRepository,
) -> None:
    s = await make_session(repo)
    streaming = ChatMessage(
        session_id=s.id,
        role="assistant",
        content="partial",
        metadata=MessageMetadata().with_extensions(status=STATUS_STREAMING),
    )
    complete = ChatMessage(
        session_id=s.id,
        role="assistant",
        content="ok",
        metadata=MessageMetadata().with_extensions(status="complete"),
    )
    await repo.append_messages(s.id, [draft(s.id), streaming, complete])

    assert await repo.fail_streaming_messages("restarted") == 1
    assert await repo.fail_streaming_messages("restarted") == 0

    full = await repo.get_session(s.id)
    assert full is not None
    by_content = {m.content: m for m in full.messages}
    assert by_content["partial"].metadata.status == STATUS_ERROR
    assert by_content["partial"].metadata.extensions["error"]["code"] == "interrupted"
    assert by_content["ok"].metadata.status == "complete"


async def test_timestamps_round_trip_as_utc(repo: SqlAlchemyChatRepository) -> None:
    s = await make_session(repo)
    (m,) = await repo.append_messages(
        s.id, [replace(draft(s.id), created_at=utcnow() - timedelta(days=1))]
    )
    full = await repo.get_session(s.id)
    assert full is not None
    assert full.messages[0].created_at == m.created_at
    assert full.messages[0].created_at.utcoffset() == timedelta(0)


async def test_engine_factory_works_for_a_fresh_database(tmp_path: Path) -> None:
    url = resolve_database_url("sqlite+aiosqlite:///sub/dir/x.db", tmp_path)
    upgrade_database(url)  # creates the directory too
    eng = create_engine(url)
    try:
        async with eng.connect() as conn:
            tables = (
                (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")))
                .scalars()
                .all()
            )
        assert {"chat_sessions", "chat_messages", "alembic_version"} <= set(tables)
    finally:
        await eng.dispose()
