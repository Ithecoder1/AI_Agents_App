"""Unit tests for chat application services:

- ConversationContextBuilder (token hygiene, stripping heavy artifacts)
- MessageStreamAggregator (pure folding, boundary detection)
- BufferedMessageWriter (flush intervals & boundaries)
- ChatSessionService & ChatTurnService
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from app.application.buffered_writer import BufferedMessageWriter
from app.application.chat_sessions import ChatSessionService
from app.application.chat_turns import ChatTurnService
from app.application.conversation_context import ConversationContextBuilder
from app.application.stream_aggregator import MessageStreamAggregator
from app.domain.chat import (
    STATUS_COMPLETE,
    STATUS_ERROR,
    STATUS_STREAMING,
    ChatMessage,
    ContextSnapshot,
    MessageMetadata,
    ThoughtStep,
    ToolCallRecord,
)
from app.domain.enums import EventType
from app.domain.errors import ConflictError, NotFoundError
from app.domain.models import ChatTurn, StreamEvent
from app.infrastructure.persistence import SqlAlchemyChatRepository

from tests.persistence.conftest import make_session


# --------------------------------------------------------------------------- #
# ConversationContextBuilder
# --------------------------------------------------------------------------- #
def test_conversation_context_builder_strips_heavy_artifacts_and_non_complete() -> None:
    builder = ConversationContextBuilder()

    messages = [
        ChatMessage(
            id="m1",
            session_id="s1",
            seq=1,
            role="user",
            content="Hello there",
        ),
        ChatMessage(
            id="m2",
            session_id="s1",
            seq=2,
            role="assistant",
            content="Hi! Let me inspect the project.",
            metadata=MessageMetadata(
                context_items=(
                    ContextSnapshot(kind="file", label="spec.md", content="Huge spec..."),
                ),
                thoughts=(ThoughtStep(text="Deep reasoning 1"),),
                tool_calls=(ToolCallRecord(name="read_file", args={"path": "a.txt"}),),
            ).with_extensions(status=STATUS_COMPLETE),
        ),
        ChatMessage(
            id="m3",
            session_id="s1",
            seq=3,
            role="assistant",
            content="Partial failure...",
            metadata=MessageMetadata().with_extensions(status=STATUS_ERROR),
        ),
        ChatMessage(
            id="m4",
            session_id="s1",
            seq=4,
            role="tool",
            content="Tool output",
        ),
        ChatMessage(
            id="m5",
            session_id="s1",
            seq=5,
            role="user",
            content="Can you refactor that?",
        ),
    ]

    history = builder.build_history(messages)
    assert len(history) == 3
    assert history == (
        ChatTurn("user", "Hello there"),
        ChatTurn("assistant", "Hi! Let me inspect the project."),
        ChatTurn("user", "Can you refactor that?"),
    )


# --------------------------------------------------------------------------- #
# MessageStreamAggregator
# --------------------------------------------------------------------------- #
def test_message_stream_aggregator_events_and_boundaries() -> None:
    now = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)
    aggregator = MessageStreamAggregator(clock=lambda: now)

    t1 = StreamEvent.create("task-1", 1, EventType.TOKEN, text="Hello ")
    assert aggregator.apply(t1) is False
    assert aggregator.content == "Hello "

    th1 = StreamEvent.create("task-1", 1, EventType.THOUGHT, text="Thinking 1")
    assert aggregator.apply(th1) is True  # new thought opens -> boundary

    th2 = StreamEvent.create("task-1", 1, EventType.THOUGHT, text=" continued")
    assert aggregator.apply(th2) is False  # continuation -> not boundary

    p1 = StreamEvent.create(
        "task-1",
        1,
        EventType.PLAN,
        summary="Refactor",
        steps=[{"title": "Step 1", "status": "pending"}],
    )
    assert aggregator.apply(p1) is True

    tc1 = StreamEvent.create(
        "task-1", 1, EventType.TOOL_CALL, name="run_tests", id="tc-1", args={"cmd": "pytest"}
    )
    assert aggregator.apply(tc1) is True

    tr1 = StreamEvent.create(
        "task-1", 1, EventType.TOOL_RESULT, id="tc-1", name="run_tests", result="all passed"
    )
    assert aggregator.apply(tr1) is True

    log1 = StreamEvent.create("task-1", 1, EventType.LOG, text="Compiling...", stream="stdout")
    assert aggregator.apply(log1) is True

    u1 = StreamEvent.create(
        "task-1",
        1,
        EventType.USAGE,
        prompt_tokens=50,
        completion_tokens=25,
        reasoning_tokens=10,
        cost_usd=0.002,
    )
    assert aggregator.apply(u1) is False

    d1 = StreamEvent.create("task-1", 1, EventType.DONE, finish_reason="stop")
    assert aggregator.apply(d1) is True

    meta = aggregator.metadata(status=STATUS_COMPLETE)
    assert len(meta.thoughts) == 1
    assert meta.thoughts[0].text == "Thinking 1 continued"
    assert meta.plan is not None
    assert meta.plan.summary == "Refactor"
    assert len(meta.plan.steps) == 1
    assert len(meta.tool_calls) == 1
    assert meta.tool_calls[0].status == "ok"
    assert meta.tool_calls[0].result == "all passed"
    assert len(meta.logs) == 1
    assert meta.logs[0].text == "Compiling..."
    assert meta.extensions["finish_reason"] == "stop"
    assert aggregator.usage.prompt_tokens == 50
    assert aggregator.usage.reasoning_tokens == 10


# --------------------------------------------------------------------------- #
# BufferedMessageWriter
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_buffered_message_writer_flushes_on_boundary_or_interval(
    repo: SqlAlchemyChatRepository,
) -> None:
    session = await make_session(repo)
    draft_msg = ChatMessage(session_id=session.id, role="assistant", content="")
    (persisted,) = await repo.append_messages(session.id, [draft_msg])

    clock_time = 100.0

    def clock() -> float:
        return clock_time

    aggregator = MessageStreamAggregator()
    writer = BufferedMessageWriter(repo, persisted.id, aggregator, interval_s=0.5, clock=clock)

    # 1. Token without boundary: not flushed yet
    aggregator.apply(StreamEvent.create("t1", 1, EventType.TOKEN, text="abc"))
    await writer.after_event(EventType.TOKEN, boundary=False)
    assert writer.flush_count == 0

    # 2. Advance time past 500ms: should flush
    clock_time = 100.6
    await writer.after_event(EventType.TOKEN, boundary=False)
    assert writer.flush_count == 1

    # Verify DB content
    s = await repo.get_session(session.id)
    assert s is not None and s.messages[0].content == "abc"

    # 3. Boundary event: should flush immediately
    aggregator.apply(StreamEvent.create("t1", 1, EventType.PLAN, summary="Plan 1", steps=[]))
    await writer.after_event(EventType.PLAN, boundary=True)
    assert writer.flush_count == 2


# --------------------------------------------------------------------------- #
# ChatSessionService & ChatTurnService
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_chat_session_service_operations(repo: SqlAlchemyChatRepository) -> None:
    svc = ChatSessionService(repo)

    session = await svc.create_session(
        project_id="test-proj",
        provider_id="fake",
        model_name="fake-model",
        title="Custom Chat",
    )
    assert session.title == "Custom Chat"
    assert session.project_id == "test-proj"

    # List
    sessions = await svc.list_sessions("test-proj")
    assert len(sessions) == 1
    assert sessions[0].id == session.id

    # Rename
    renamed = await svc.rename_session(session.id, "Updated Title")
    assert renamed.title == "Updated Title"

    # Delete
    await svc.delete_session(session.id)
    with pytest.raises(NotFoundError):
        await svc.get_session(session.id)


@pytest.mark.asyncio
async def test_chat_turn_service_end_to_end(
    repo: SqlAlchemyChatRepository,
) -> None:
    from app.api.composition import default_registry, default_workspace_manager

    registry, _ = default_registry()
    workspaces = default_workspace_manager()
    session_svc = ChatSessionService(repo)
    turn_svc = ChatTurnService(session_svc, repo, registry, workspaces, flush_interval_s=0.1)

    session = await session_svc.create_session(
        project_id="root",
        provider_id="fake",
        model_name="fake-model",
    )

    started = await turn_svc.start_turn(
        session.id,
        "Hello assistant, say hi!",
    )
    assert started.user_message.content == "Hello assistant, say hi!"
    assert started.assistant_message.metadata.status == STATUS_STREAMING

    # Conflict check
    with pytest.raises(ConflictError):
        await turn_svc.start_turn(session.id, "Another turn while running")

    # Collect events from turn stream
    events: list[StreamEvent] = []
    async for ev in started.stream.subscribe():
        events.append(ev)

    assert any(e.type == EventType.TOKEN for e in events)
    assert any(e.type == EventType.DONE for e in events)

    # Let the background task finish persisting
    await asyncio.sleep(0.1)

    updated_session = await session_svc.get_session(session.id)
    assert updated_session.title.startswith("Hello assistant")
    assert updated_session.telemetry.message_count == 2
    assert updated_session.telemetry.total_tokens > 0

    assistant_msg = updated_session.messages[1]
    assert assistant_msg.metadata.status == STATUS_COMPLETE
    assert "fake:" in assistant_msg.content
    assert len(assistant_msg.metadata.context_items) > 0  # Context files snapshot captured!
