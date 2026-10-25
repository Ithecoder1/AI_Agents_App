"""FastAPI REST and WebSocket API for Vibe Forge.

Acts as the composition root and presentation layer for web and CLI clients.
Serves static web assets and provides real-time streaming endpoints.
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vibe.api.composition import default_registry, default_workspace_manager, repo_root
from vibe.application.prompt_linter import lint_prompt
from vibe.application.registry import ProviderState
from vibe.domain.enums import EventType
from vibe.domain.errors import ProviderError, ValidationError
from vibe.domain.models import ExecutionParams, ExecutionTask, Prompt

# Initialize registry, settings, and workspace manager
registry, settings = default_registry()
workspace_manager = default_workspace_manager()

app = FastAPI(
    title="Vibe Forge API",
    version="0.1.0",
    description="Multi-provider vibe-coding execution engine and prompt studio",
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
        "vibe.api.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=True,
    )
