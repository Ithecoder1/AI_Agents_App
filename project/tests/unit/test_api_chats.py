"""API unit & integration tests for multi-turn chat sessions and streaming events."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from app.api.main import app
from starlette.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_list_workspace_chats(client: TestClient) -> None:
    # Get available workspaces to grab a valid project_id
    res = client.get("/api/workspaces")
    assert res.status_code == 200
    workspaces = res.json()
    assert len(workspaces) >= 1
    project_id = workspaces[0]["project_id"]

    # Initial list is accessible
    list_res = client.get(f"/api/workspaces/{project_id}/chats")
    assert list_res.status_code == 200
    initial_count = len(list_res.json())

    # Create a chat session
    create_res = client.post(
        f"/api/workspaces/{project_id}/chats",
        json={"title": "Test Chat Listing", "provider_id": "fake", "model": "fake-model"},
    )
    assert create_res.status_code == 201
    created = create_res.json()
    assert created["title"] == "Test Chat Listing"
    assert created["project_id"] == project_id
    assert "messages" not in created  # Summary dict does not fetch messages
    session_id = created["id"]

    try:
        # Check list updated
        after_res = client.get(f"/api/workspaces/{project_id}/chats")
        assert after_res.status_code == 200
        after_chats = after_res.json()
        assert len(after_chats) == initial_count + 1
        found = next((c for c in after_chats if c["id"] == session_id), None)
        assert found is not None
        assert found["title"] == "Test Chat Listing"
        assert found["message_count"] == 0
    finally:
        client.delete(f"/api/chats/{session_id}")


def test_create_get_rename_delete_chat(client: TestClient) -> None:
    # 1. Create chat session
    create_res = client.post(
        "/api/workspaces/root/chats",
        json={"title": "Initial Title", "provider_id": "fake", "model": "fake-model"},
    )
    assert create_res.status_code == 201
    session_id = create_res.json()["id"]

    # 2. Get full chat session details
    get_res = client.get(f"/api/chats/{session_id}")
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["id"] == session_id
    assert data["title"] == "Initial Title"
    assert data["messages"] == []
    assert data["telemetry"]["total_tokens"] == 0

    # 3. Rename chat session
    patch_res = client.patch(
        f"/api/chats/{session_id}",
        json={"title": "Renamed Title"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["title"] == "Renamed Title"

    verify_res = client.get(f"/api/chats/{session_id}")
    assert verify_res.json()["title"] == "Renamed Title"

    # 4. Delete chat session
    del_res = client.delete(f"/api/chats/{session_id}")
    assert del_res.status_code == 200
    assert del_res.json() == {"ok": True, "id": session_id}

    # Verify 404 after deletion
    assert client.get(f"/api/chats/{session_id}").status_code == 404
    assert client.patch(f"/api/chats/{session_id}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/chats/{session_id}").status_code == 404


@pytest.mark.asyncio
async def test_send_message_and_execution_lifecycle() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Create session
        create_res = await ac.post(
            "/api/workspaces/root/chats",
            json={"title": "Multi-turn Exec", "provider_id": "fake", "model": "fake-model"},
        )
        assert create_res.status_code == 201
        session_id = create_res.json()["id"]

        try:
            # Send user message -> 202 Accepted
            msg_res = await ac.post(
                f"/api/chats/{session_id}/messages",
                json={"user_prompt": "First message to fake assistant"},
            )
            assert msg_res.status_code == 202
            payload = msg_res.json()
            assert payload["session_id"] == session_id
            assert payload["user_message"]["content"] == "First message to fake assistant"
            assert payload["user_message"]["role"] == "user"
            assert payload["assistant_message"]["role"] == "assistant"
            assert payload["assistant_message"]["status"] == "streaming"
            assert payload["cursor"] == 0

            # Wait for asynchronous execution turn to finish
            await asyncio.sleep(0.4)

            # Query session to verify messages and telemetry updated in DB
            session_res = await ac.get(f"/api/chats/{session_id}")
            assert session_res.status_code == 200
            full_session = session_res.json()
            assert len(full_session["messages"]) == 2
            user_msg, assistant_msg = full_session["messages"]
            assert user_msg["role"] == "user"
            assert user_msg["content"] == "First message to fake assistant"
            assert assistant_msg["role"] == "assistant"
            assert assistant_msg["status"] == "complete"
            assert len(assistant_msg["content"]) > 0
            assert full_session["telemetry"]["message_count"] == 2
            assert full_session["telemetry"]["total_tokens"] > 0

        finally:
            await ac.delete(f"/api/chats/{session_id}")


def test_cancel_chat_turn(client: TestClient) -> None:
    create_res = client.post(
        "/api/workspaces/root/chats",
        json={"title": "Cancel Turn Test", "provider_id": "fake"},
    )
    assert create_res.status_code == 201
    session_id = create_res.json()["id"]
    try:
        # When no active turn is running, cancel returns cancelled=False
        cancel_res = client.post(f"/api/chats/{session_id}/cancel")
        assert cancel_res.status_code == 200
        assert cancel_res.json() == {"session_id": session_id, "cancelled": False}
    finally:
        client.delete(f"/api/chats/{session_id}")


def test_websocket_chat_events_replay_finished(client: TestClient) -> None:
    create_res = client.post(
        "/api/workspaces/root/chats",
        json={"title": "WS Replay Test", "provider_id": "fake"},
    )
    session_id = create_res.json()["id"]
    try:
        # Connecting to a session with no active stream closes cleanly
        with client.websocket_connect(f"/api/chats/{session_id}/events"):
            pass
    finally:
        client.delete(f"/api/chats/{session_id}")


def test_chat_not_found_handling(client: TestClient) -> None:
    assert client.get("/api/workspaces/non-existent-proj/chats").status_code == 404
    assert (
        client.post(
            "/api/workspaces/non-existent-proj/chats", json={"title": "X", "provider_id": "fake"}
        ).status_code
        == 404
    )
    assert client.get("/api/chats/unknown-session-id").status_code == 404
    assert client.delete("/api/chats/unknown-session-id").status_code == 404
