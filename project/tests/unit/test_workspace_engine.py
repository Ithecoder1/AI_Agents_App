from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from app.api.main import app
from app.application.workspace_manager import WorkspaceManager
from app.domain.errors import ValidationError
from app.domain.models import ProjectManifest, WorkspaceContext
from app.infrastructure.workspace import (
    YamlManifestParser,
    safe_git_diff,
    safe_read_file,
    safe_run_command,
    safe_write_file,
)
from starlette.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_project_manifest_serialization() -> None:
    manifest = ProjectManifest(
        name="test-subproject",
        version="1.2.0",
        description="Demo project",
        runtime="node:24",
        entrypoint="src/index.js",
        test_command="npm test",
        context_files=("README.md", "architecture.md"),
        allowed_roots=("./",),
    )
    data = manifest.to_dict()
    assert data["name"] == "test-subproject"
    assert data["version"] == "1.2.0"
    assert data["runtime"] == "node:24"

    reconstructed = ProjectManifest.from_dict(data)
    assert reconstructed.name == manifest.name
    assert reconstructed.runtime == manifest.runtime
    assert reconstructed.context_files == manifest.context_files


def test_workspace_context_path_traversal_guardrail() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        manifest = ProjectManifest(name="sandbox-test")
        ctx = WorkspaceContext(project_id="test", name="Sandbox", root_path=root, manifest=manifest)

        # Valid subpaths
        assert ctx.validate_path("src/app.py") == (root / "src/app.py").resolve()
        assert ctx.validate_path(root / "README.md") == (root / "README.md").resolve()

        # Path traversal attacks MUST raise ValidationError
        with pytest.raises(ValidationError, match="Path traversal detected"):
            ctx.validate_path("../outside.txt")

        with pytest.raises(ValidationError, match="Path traversal detected"):
            ctx.validate_path("../../etc/passwd")


def test_sandboxed_tools() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        manifest = ProjectManifest(name="sandbox-tools")
        ctx = WorkspaceContext(
            project_id="test-tools", name="Sandbox Tools", root_path=root, manifest=manifest
        )

        # Safe write & read
        written = safe_write_file(ctx, "sub/hello.txt", "Hello Sandbox")
        assert written.is_file()
        assert safe_read_file(ctx, "sub/hello.txt") == "Hello Sandbox"

        # Traversal write blocked
        with pytest.raises(ValidationError):
            safe_write_file(ctx, "../escape.txt", "malicious")

        # Traversal read blocked
        with pytest.raises(ValidationError):
            safe_read_file(ctx, "../escape.txt")

        # Command runner
        proc = safe_run_command(ctx, ["python", "-c", "import os; print(os.getcwd())"])
        assert proc.returncode == 0
        assert str(root.resolve()).lower() in proc.stdout.strip().lower()

        # Git diff (clean folder)
        assert safe_git_diff(ctx) == ""


def test_yaml_manifest_parser_and_persistence() -> None:
    parser = YamlManifestParser()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        assert parser.parse(root) is None

        manifest = ProjectManifest(
            name="persisted-proj",
            version="0.5.0",
            description="Testing YAML",
            runtime="python:3.10",
        )
        saved_file = parser.save(root, manifest)
        assert saved_file.is_file()

        loaded = parser.parse(root)
        assert loaded is not None
        assert loaded.name == "persisted-proj"
        assert loaded.version == "0.5.0"


def test_workspace_manager_self_and_auto_synthesis() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        parser = YamlManifestParser()
        manager = WorkspaceManager(root, manifest_parser=parser)

        # Self workspace registered
        self_ws = manager.get_workspace("root")
        assert self_ws.is_self is True
        assert manager.get_workspace("self").project_id == "root"
        assert manager.get_workspace(None).project_id == "root"

        # Auto-synthesize node project
        node_dir = root / "node_proj"
        node_dir.mkdir()
        (node_dir / "package.json").write_text('{"name": "my-node-app", "main": "index.js"}')
        (node_dir / "README.md").write_text("# My Node App\nDocumentation.")

        node_ws = manager.register_workspace(node_dir, project_id="node-proj")
        assert node_ws.manifest.runtime == "node:24"
        assert node_ws.manifest.name == "my-node-app"
        assert "README.md" in node_ws.manifest.context_files

        # Context loading
        summaries = manager.load_context_summaries(node_ws)
        assert "README.md" in summaries
        assert "Documentation" in summaries["README.md"]

        # Prompt enrichment
        enriched = manager.enrich_system_instructions(node_ws, "Be helpful.")
        assert "Active Target Workspace Context" in enriched
        assert "my-node-app" in enriched
        assert "README.md" in enriched


def test_api_workspace_endpoints(client: TestClient) -> None:
    # 1. List workspaces (must include root)
    res = client.get("/api/workspaces")
    assert res.status_code == 200
    workspaces = res.json()
    assert any(w["project_id"] == "root" and w["is_self"] is True for w in workspaces)

    # 2. Register temporary workspace
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "subproject"
        target.mkdir()
        (target / "pyproject.toml").write_text("[project]\nname = 'subpkg'")
        (target / "README.md").write_text("# Subproject Guide")

        reg_res = client.post(
            "/api/workspaces",
            json={"path": str(target), "project_id": "test-sub", "name": "Subproject Alpha"},
        )
        assert reg_res.status_code == 200
        data = reg_res.json()
        assert data["project_id"] == "test-sub"
        assert data["name"] == "Subproject Alpha"
        assert data["manifest"]["runtime"] == "python:3.10"

        # 3. Get workspace details
        get_res = client.get("/api/workspaces/test-sub")
        assert get_res.status_code == 200
        assert get_res.json()["project_id"] == "test-sub"

        # 4. Get tree
        tree_res = client.get("/api/workspaces/test-sub/tree")
        assert tree_res.status_code == 200
        tree = tree_res.json()
        assert "children" in tree
        assert any(child["name"] == "pyproject.toml" for child in tree["children"])

        # 5. Execute scoped task
        exec_res = client.post(
            "/api/workspaces/test-sub/execute",
            json={"user_prompt": "Refactor code", "provider_id": "fake"},
        )
        assert exec_res.status_code == 200
        exec_data = exec_res.json()
        assert exec_data["status"] == "succeeded"
        assert exec_data["workspace_id"] == "test-sub"

        # 6. Scoped WebSocket event stream
        with client.websocket_connect("/api/workspaces/test-sub/events") as ws:
            ws.send_json({"provider_id": "fake", "user_prompt": "Stream scoped task"})
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
