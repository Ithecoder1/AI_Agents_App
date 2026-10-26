from __future__ import annotations

import pytest
from app.api.main import app
from starlette.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_api_health_endpoint(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "version": "0.1.0"}


def test_api_providers_endpoint(client: TestClient) -> None:
    res = client.get("/api/providers")
    assert res.status_code == 200
    providers = res.json()
    assert isinstance(providers, list)
    assert any(p["id"] == "fake" and p["state"] == "ready" for p in providers)


def test_api_prompt_lint_endpoint(client: TestClient) -> None:
    res = client.post(
        "/api/prompts/lint",
        json={"user_prompt": "Test prompt", "system_instructions": "Role info"},
    )
    assert res.status_code == 200
    report = res.json()
    assert "score" in report
    assert "token_estimate" in report


def test_api_execute_endpoint(client: TestClient) -> None:
    res = client.post(
        "/api/execute",
        json={"user_prompt": "Build pool", "provider_id": "fake"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "succeeded"
    assert "output_text" in data
    assert len(data["artifacts"]) >= 1


def test_web_static_assets_served(client: TestClient) -> None:
    html_res = client.get("/")
    assert html_res.status_code == 200
    assert "Vibe Forge" in html_res.text

    css_res = client.get("/assets/css/style.css")
    assert css_res.status_code == 200
    assert "--bg-primary" in css_res.text

    js_res = client.get("/assets/js/app.js")
    assert js_res.status_code == 200
    assert "VIBE FORGE WEB CLIENT" in js_res.text


def test_websocket_streaming_execution(client: TestClient) -> None:
    with client.websocket_connect("/api/ws/execute") as ws:
        ws.send_json({"provider_id": "fake", "user_prompt": "Stream this test"})
        events = []
        for _ in range(20):
            try:
                msg = ws.receive_json()
                events.append(msg["type"])
                if msg["type"] == "done":
                    break
            except Exception:
                break
        assert "status" in events
        assert "token" in events
        assert "done" in events
