"""FastAPI REST and WebSocket API for Vibe Forge.

Acts as the composition root and presentation layer for web and CLI clients.
Serves static web assets and provides real-time streaming endpoints.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.api.composition import (
    default_chat_services,
    default_registry,
    default_workspace_manager,
    repo_root,
)
from app.application.prompt_linter import lint_prompt
from app.application.registry import ProviderState
from app.domain.enums import EventType
from app.domain.errors import ConflictError, NotFoundError, ProviderError, ValidationError
from app.domain.models import ExecutionParams, ExecutionTask, Prompt

# Initialize registry, settings, workspace manager and chat persistence services
registry, settings = default_registry()
workspace_manager = default_workspace_manager()
db_engine, chat_repo, chat_session_service, chat_turn_service = default_chat_services(
    settings, registry, workspace_manager
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Recover any assistant messages left streaming by a previous crash/shutdown
    await chat_turn_service.recover_interrupted()
    yield
    await chat_turn_service.shutdown()
    await db_engine.dispose()
    await registry.aclose()


app = FastAPI(
    title="Vibe Forge API",
    version="0.1.0",
    description="Multi-provider vibe-coding execution engine and prompt studio",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.security.cors_origins) or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request/Response Schemas ---------------------------------------------- #
class LintRequest(BaseModel):
    user_prompt: str = Field(..., description="User prompt text")
    system_instructions: str = Field("", description="System instructions / role")
    max_tokens: int | None = Field(None, description="Optional token budget limit")


class ExecuteRequest(BaseModel):
    user_prompt: str
    system_instructions: str = ""
    provider_id: str = "fake"
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    workspace_id: str | None = None


class RegisterWorkspaceRequest(BaseModel):
    path: str = Field(..., description="Absolute or relative path to project directory")
    project_id: str | None = Field(None, description="Optional custom workspace ID")
    name: str | None = Field(None, description="Optional project display name")
    persist_manifest: bool = Field(False, description="Whether to persist synthesized manifest")


class CreateChatRequest(BaseModel):
    title: str | None = Field(None, description="Optional custom chat title")
    provider_id: str = Field("fake", description="Target provider ID")
    model: str | None = Field(None, description="Optional target model")


class RenameChatRequest(BaseModel):
    title: str = Field(..., description="New title for the chat session")


class SendChatMessageRequest(BaseModel):
    user_prompt: str = Field(..., description="User prompt text")
    system_instructions: str = Field("", description="Optional turn-specific system instructions")
    provider_id: str | None = Field(None, description="Optional provider override")
    model: str | None = Field(None, description="Optional model override")
    temperature: float | None = Field(None, description="Optional temperature override")
    max_tokens: int | None = Field(None, description="Optional max tokens override")


# --- REST Endpoints -------------------------------------------------------- #
@app.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/providers")
async def list_providers() -> list[dict[str, Any]]:
    """List all configured providers with current health status and capabilities."""
    entries = registry.entries()
    providers: list[dict[str, Any]] = []

    for entry in entries:
        is_ready = entry.state is ProviderState.READY and entry.instance is not None
        health = await registry.health(entry.config.id) if is_ready else None
        caps = entry.instance.capabilities if is_ready and entry.instance else None

        providers.append(
            {
                "id": entry.config.id,
                "kind": entry.config.kind,
                "display_name": entry.config.display_name or entry.config.id,
                "state": entry.state.value,
                "enabled": entry.config.enabled,
                "error": entry.error,
                "health": {
                    "ok": health.ok if health else False,
                    "reason": health.reason if health else (entry.error or "Disabled"),
                    "latency_ms": health.latency_ms if health else None,
                },
                "capabilities": {
                    "streaming": caps.streaming if caps else False,
                    "supports_cancel": caps.supports_cancel if caps else False,
                    "idempotent": caps.idempotent if caps else False,
                    "can_modify_workspace": caps.can_modify_workspace if caps else False,
                    "reports_usage": caps.reports_usage if caps else False,
                    "models": list(caps.models) if caps else [],
                },
                "options": entry.config.safe_options(),
            }
        )
    return providers


# --- Workspace Endpoints --------------------------------------------------- #
@app.get("/api/workspaces")
async def list_workspaces_endpoint() -> list[dict[str, Any]]:
    """List registered workspaces (id, display name, path, manifest, is_self)."""
    return [ws.to_dict() for ws in workspace_manager.list_workspaces()]


@app.post("/api/workspaces")
async def register_workspace_endpoint(request: RegisterWorkspaceRequest) -> dict[str, Any]:
    """Register a new target project directory."""
    try:
        ws = workspace_manager.register_workspace(
            path=request.path,
            project_id=request.project_id,
            name=request.name,
            persist_manifest=request.persist_manifest,
        )
        return dict(ws.to_dict())
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc


@app.get("/api/workspaces/{workspace_id}")
async def get_workspace_endpoint(workspace_id: str) -> dict[str, Any]:
    """Retrieve workspace details and manifest."""
    try:
        ws = workspace_manager.get_workspace(workspace_id)
        return dict(ws.to_dict())
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc


@app.get("/api/workspaces/{workspace_id}/tree")
async def get_workspace_tree_endpoint(workspace_id: str, depth: int = 3) -> dict[str, Any]:
    """Return filtered file tree for the workspace."""
    try:
        ws = workspace_manager.get_workspace(workspace_id)
        return dict(workspace_manager.scan_tree(ws, max_depth=min(depth, 5)))
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc


@app.post("/api/workspaces/{workspace_id}/execute")
async def execute_workspace_task(workspace_id: str, request: ExecuteRequest) -> dict[str, Any]:
    """Execute a task scoped strictly to the specified workspace."""
    request.workspace_id = workspace_id
    return await execute_task(request)


# --- Chat Session & Multi-Turn Endpoints ----------------------------------- #
@app.get("/api/workspaces/{project_id}/chats")
async def list_workspace_chats_endpoint(
    project_id: str, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    """Fast query listing all chat sessions for the workspace (lightweight, no message payloads)."""
    try:
        ws = workspace_manager.get_workspace(project_id)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc

    sessions = await chat_session_service.list_sessions(ws.project_id, limit=limit, offset=offset)
    return [s.to_summary_dict() for s in sessions]


@app.post("/api/workspaces/{project_id}/chats", status_code=201)
async def create_workspace_chat_endpoint(
    project_id: str, request: CreateChatRequest
) -> dict[str, Any]:
    """Create a new chat session scoped strictly to the specified workspace."""
    try:
        ws = workspace_manager.get_workspace(project_id)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc

    try:
        session = await chat_session_service.create_session(
            project_id=ws.project_id,
            provider_id=request.provider_id,
            model_name=request.model or "default",
            title=request.title,
        )
        return session.to_summary_dict()
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"error": str(exc)}) from exc


@app.get("/api/chats/{session_id}")
async def get_chat_session_endpoint(session_id: str) -> dict[str, Any]:
    """Fetch full chat session details, telemetry, and ordered messages with rich metadata."""
    try:
        session = await chat_session_service.get_session(session_id, with_messages=True)
        return session.to_dict()
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc


@app.patch("/api/chats/{session_id}")
async def rename_chat_session_endpoint(
    session_id: str, request: RenameChatRequest
) -> dict[str, Any]:
    """Rename a chat session title."""
    try:
        session = await chat_session_service.rename_session(session_id, request.title)
        return session.to_summary_dict()
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"error": str(exc)}) from exc


@app.delete("/api/chats/{session_id}")
async def delete_chat_session_endpoint(session_id: str) -> dict[str, Any]:
    """Delete a chat session (cascades to all associated chat messages)."""
    try:
        await chat_turn_service.cancel_turn(session_id)
        await chat_session_service.delete_session(session_id)
        return {"ok": True, "id": session_id}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc


@app.post("/api/chats/{session_id}/messages", status_code=202)
async def send_chat_message_endpoint(
    session_id: str, request: SendChatMessageRequest
) -> dict[str, Any]:
    """Append a user message, trigger multi-turn agent execution, and return stream cursor."""
    try:
        started = await chat_turn_service.start_turn(
            session_id,
            request.user_prompt,
            system_instructions=request.system_instructions,
            provider_id=request.provider_id,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        return {
            "session_id": session_id,
            "user_message": started.user_message.to_dict(),
            "assistant_message": started.assistant_message.to_dict(),
            "stream_url": f"/api/chats/{session_id}/events",
            "message_id": started.assistant_message.id,
            "cursor": 0,
        }
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"error": str(exc)}) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=400, detail={"error": exc.message, "code": exc.code}
        ) from exc


@app.post("/api/chats/{session_id}/cancel")
async def cancel_chat_turn_endpoint(session_id: str) -> dict[str, Any]:
    """Cancel any active in-flight execution turn for the chat session."""
    cancelled = await chat_turn_service.cancel_turn(session_id)
    return {"session_id": session_id, "cancelled": cancelled}


@app.post("/api/prompts/lint")
async def lint_prompt_endpoint(request: LintRequest) -> dict[str, Any]:
    """Run deterministic prompt quality linter."""
    prompt = Prompt(
        user_prompt=request.user_prompt,
        system_instructions=request.system_instructions,
    )
    report = lint_prompt(prompt, max_tokens=request.max_tokens)
    return report.to_dict()


@app.post("/api/execute")
async def execute_task(request: ExecuteRequest) -> dict[str, Any]:
    """Execute a task and return the complete result."""
    try:
        provider = registry.resolve(request.provider_id)
    except ProviderError as exc:
        raise HTTPException(
            status_code=400, detail={"error": exc.message, "code": exc.code}
        ) from exc

    try:
        ws = workspace_manager.get_workspace(request.workspace_id)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc

    enriched_system = workspace_manager.enrich_system_instructions(ws, request.system_instructions)
    prompt = Prompt(
        user_prompt=request.user_prompt,
        system_instructions=enriched_system,
    )
    try:
        params = ExecutionParams(
            provider_id=request.provider_id,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"error": str(exc)}) from exc

    task = ExecutionTask.create(
        prompt=prompt,
        params=params,
        workspace=str(ws.root_path),
    ).start(request.provider_id)
    result = await provider.execute(task)

    return {
        "task_id": result.task_id,
        "status": result.status.value,
        "provider_id": result.provider_id,
        "workspace_id": ws.project_id,
        "output_text": result.output_text,
        "artifacts": [a.to_dict() for a in result.artifacts],
        "usage": result.usage.to_dict(),
        "duration_ms": result.duration_ms,
        "error": result.error.to_dict() if result.error else None,
    }


# --- WebSocket Streaming Endpoints ----------------------------------------- #
async def _handle_websocket_execution(
    websocket: WebSocket,
    explicit_workspace_id: str | None = None,
) -> None:
    await websocket.accept()

    try:
        data = await websocket.receive_json()
        provider_id = data.get("provider_id", "fake")
        user_prompt = data.get("user_prompt", "")
        system_instructions = data.get("system_instructions", "")
        model = data.get("model")
        ws_id = explicit_workspace_id or data.get("workspace_id")

        try:
            provider = registry.resolve(provider_id)
        except Exception as exc:
            await websocket.send_json(
                {
                    "type": EventType.ERROR.value,
                    "data": {"message": f"Provider resolution error: {exc}"},
                }
            )
            await websocket.close()
            return

        try:
            ws = workspace_manager.get_workspace(ws_id)
        except ValidationError as exc:
            await websocket.send_json(
                {
                    "type": EventType.ERROR.value,
                    "data": {"message": f"Workspace resolution error: {exc}"},
                }
            )
            await websocket.close()
            return

        enriched_system = workspace_manager.enrich_system_instructions(ws, system_instructions)
        prompt = Prompt(user_prompt=user_prompt, system_instructions=enriched_system)
        params = ExecutionParams(provider_id=provider_id, model=model)
        task = ExecutionTask.create(
            prompt=prompt,
            params=params,
            workspace=str(ws.root_path),
        ).start(provider_id)

        # Stream events directly to browser client
        seq = 1
        async for event in provider.stream_execute(task):
            event_with_seq = event.with_seq(seq)
            seq += 1
            await websocket.send_json(event_with_seq.to_dict())

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {
                    "type": EventType.ERROR.value,
                    "data": {"message": f"Execution error: {exc}"},
                }
            )


@app.websocket("/api/ws/execute")
async def websocket_execute(websocket: WebSocket) -> None:
    """Real-time bidirectional WebSocket execution stream (workspace in payload or default)."""
    await _handle_websocket_execution(websocket)


@app.websocket("/api/workspaces/{workspace_id}/events")
async def websocket_workspace_events(websocket: WebSocket, workspace_id: str) -> None:
    """Real-time execution stream scoped strictly to a specific workspace."""
    await _handle_websocket_execution(websocket, explicit_workspace_id=workspace_id)


@app.websocket("/api/chats/{session_id}/events")
async def websocket_chat_session_events(
    websocket: WebSocket,
    session_id: str,
    cursor: int = 0,
    message_id: str | None = None,
) -> None:
    """Stream live execution events for a chat session.

    Supports resuming from a specific sequence cursor (?cursor=N) or targeting
    a specific message (?message_id=...). Client may send {"action": "cancel"}
    to cancel the current turn.
    """
    await websocket.accept()

    stream = None
    if message_id:
        stream = chat_turn_service.get_stream(message_id)
    if stream is None:
        active = chat_turn_service._active.get(session_id)
        if active is not None:
            stream = active.stream

    if stream is None:
        try:
            session = await chat_session_service.get_session(session_id, with_messages=True)
        except NotFoundError:
            await websocket.send_json(
                {
                    "type": EventType.ERROR.value,
                    "data": {"message": f"Chat session {session_id!r} not found"},
                }
            )
            await websocket.close()
            return

        last_msg = session.messages[-1] if session.messages else None
        if last_msg and last_msg.role == "assistant":
            await websocket.send_json(
                {
                    "type": EventType.DONE.value,
                    "data": {
                        "message_id": last_msg.id,
                        "status": last_msg.metadata.status,
                        "usage": last_msg.usage.to_dict(),
                        "finish_reason": last_msg.metadata.extensions.get("finish_reason", "stop"),
                    },
                    "seq": 1,
                }
            )
        await websocket.close()
        return

    async def listen_incoming() -> None:
        try:
            while True:
                msg = await websocket.receive_json()
                if msg.get("action") == "cancel":
                    await chat_turn_service.cancel_turn(session_id)
        except Exception:
            pass

    listener = asyncio.create_task(listen_incoming())
    try:
        async for event in stream.subscribe(after_seq=cursor):
            await websocket.send_json(event.to_dict())
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {
                    "type": EventType.ERROR.value,
                    "data": {"message": f"Chat stream error: {exc}"},
                }
            )
    finally:
        listener.cancel()


# --- Static Web App Mounting ----------------------------------------------- #
web_dist = repo_root() / "web"
web_assets = web_dist / "assets"
if web_assets.is_dir():
    app.mount("/assets", StaticFiles(directory=str(web_assets)), name="assets")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str) -> FileResponse:
    file_path = web_dist / full_path
    if file_path.is_file() and not full_path.startswith("api"):
        return FileResponse(file_path)
    return FileResponse(web_dist / "index.html")


if __name__ == "__main__":
    import os
    import sys
    import threading
    import webbrowser

    import uvicorn

    # Support auto-opening client browser via --open-browser or VIBE_OPEN_BROWSER=1
    if "--open-browser" in sys.argv or os.getenv("VIBE_OPEN_BROWSER", "").lower() in ("1", "true"):
        url = f"http://{settings.server.host}:{settings.server.port}"
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "app.api.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=True,
    )
