"""Workspace resolution, manifest parsing, context enrichment, and tree scanning."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.domain.chat import ContextSnapshot
from app.domain.errors import ValidationError
from app.domain.models import ProjectManifest, WorkspaceContext
from app.domain.ports.workspace import IManifestParser

_IGNORED_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".import_linter_cache",
    "dist",
    "build",
    ".next",
    ".cache",
}

_MAX_CONTEXT_FILE_BYTES = 10_000


class WorkspaceManager:
    """Manages registered workspaces, manifest resolution, context loading, and sandboxing."""

    def __init__(
        self,
        root_repo_path: Path,
        manifest_parser: IManifestParser | None = None,
    ) -> None:
        self._root_path = root_repo_path.resolve()
        self._manifest_parser = manifest_parser
        self._workspaces: dict[str, WorkspaceContext] = {}

        # Register self (root) workspace
        self._register_self_workspace()

    @property
    def root_path(self) -> Path:
        return self._root_path

    # -- Self Workspace Setup ------------------------------------------------ #
    def _register_self_workspace(self) -> None:
        manifest: ProjectManifest | None = None
        if self._manifest_parser is not None:
            manifest = self._manifest_parser.parse(self._root_path)

        if manifest is None:
            manifest = self.synthesize_manifest(self._root_path, name="vibe-forge")

        self_ctx = WorkspaceContext(
            project_id="root",
            name=manifest.name or "Vibe Forge (Self)",
            root_path=self._root_path,
            manifest=manifest,
            is_self=True,
        )
        self._workspaces["root"] = self_ctx

    # -- Workspace Registration & Resolution -------------------------------- #
    def register_workspace(
        self,
        path: Path | str,
        project_id: str | None = None,
        name: str | None = None,
        *,
        persist_manifest: bool = False,
    ) -> WorkspaceContext:
        """Register a new target project directory.

        Validates directory existence, resolves or auto-scaffolds the manifest.
        """
        resolved_path = Path(path).resolve()
        if not resolved_path.is_dir():
            raise ValidationError(f"Target directory does not exist: {resolved_path}")

        is_self = resolved_path == self._root_path
        assigned_id = (
            "root" if is_self else (project_id or resolved_path.name.lower().replace(" ", "-"))
        )

        # 1. Locate and parse manifest
        manifest: ProjectManifest | None = None
        if self._manifest_parser is not None:
            manifest = self._manifest_parser.parse(resolved_path)

        # 2. Auto-scaffolding fallback if no manifest exists
        if manifest is None:
            manifest = self.synthesize_manifest(resolved_path, name=name)
            if persist_manifest and self._manifest_parser is not None:
                self._manifest_parser.save(resolved_path, manifest)

        ctx = WorkspaceContext(
            project_id=assigned_id,
            name=name or manifest.name or resolved_path.name,
            root_path=resolved_path,
            manifest=manifest,
            is_self=is_self,
        )
        self._workspaces[assigned_id] = ctx
        return ctx

    def get_workspace(self, project_id: str | None) -> WorkspaceContext:
        """Retrieve workspace by id, mapping 'self', 'root', or None to the self workspace."""
        if not project_id or project_id in ("self", "root"):
            return self._workspaces["root"]

        ctx = self._workspaces.get(project_id)
        if ctx is None:
            raise ValidationError(f"Workspace {project_id!r} is not registered")
        return ctx

    def list_workspaces(self) -> list[WorkspaceContext]:
        """Return all registered workspaces."""
        return list(self._workspaces.values())

    # -- Auto-Scaffolding Fallback Scanner ----------------------------------- #
    def synthesize_manifest(self, root_path: Path, name: str | None = None) -> ProjectManifest:
        """Synthesize an in-memory ProjectManifest by scanning project indicator files."""
        proj_name = name or root_path.name
        description = f"Auto-detected project at {root_path.name}"
        runtime = "python:3.10"
        entrypoint: str | None = None
        test_command: str | None = None
        context_files: list[str] = []

        # Python indicators
        pyproject = root_path / "pyproject.toml"
        requirements = root_path / "requirements.txt"
        if pyproject.is_file() or requirements.is_file():
            runtime = "python:3.10"
            test_command = "pytest"
            if (root_path / "main.py").is_file():
                entrypoint = "main.py"

        # Node / JS indicators
        pkg_json = root_path / "package.json"
        if pkg_json.is_file():
            runtime = "node:24"
            test_command = "npm test"
            try:
                raw_pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                if isinstance(raw_pkg, Mapping):
                    proj_name = str(raw_pkg.get("name") or proj_name)
                    description = str(raw_pkg.get("description") or description)
                    entrypoint = raw_pkg.get("main")
            except Exception:
                pass

        # Context files check
        for candidate in ("README.md", "architecture.md", "AGENTS.md", "spec.md", "docs/spec.md"):
            if (root_path / candidate).is_file():
                context_files.append(candidate)

        return ProjectManifest(
            name=proj_name,
            version="0.1.0",
            description=description,
            runtime=runtime,
            entrypoint=entrypoint,
            test_command=test_command,
            context_files=tuple(context_files),
            allowed_roots=("./",),
        )

    # -- Filtered Directory Tree Scanner ------------------------------------- #
    def scan_tree(
        self,
        workspace: WorkspaceContext,
        max_depth: int = 3,
    ) -> dict[str, Any]:
        """Generate a filtered directory tree respecting common ignore rules."""

        def _walk(current: Path, depth: int) -> dict[str, Any]:
            node: dict[str, Any] = {
                "name": current.name or str(current),
                "type": "directory" if current.is_dir() else "file",
                "path": str(current.relative_to(workspace.root_path)).replace("\\", "/"),
            }
            if current.is_dir() and depth < max_depth:
                children: list[dict[str, Any]] = []
                try:
                    for entry in sorted(
                        current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
                    ):
                        if entry.name in _IGNORED_DIRS:
                            continue
                        children.append(_walk(entry, depth + 1))
                except (PermissionError, OSError):
                    pass
                node["children"] = children
            return node

        return _walk(workspace.root_path, 0)

    # -- Lazy Context Loading & Prompt Enrichment ---------------------------- #
    def load_context_summaries(self, workspace: WorkspaceContext) -> dict[str, str]:
        """Read only files listed in manifest.context_files, enforcing sandbox boundaries."""
        summaries: dict[str, str] = {}
        for rel_file in workspace.manifest.context_files:
            try:
                target = workspace.validate_path(rel_file)
                if target.is_file():
                    content = target.read_text(encoding="utf-8", errors="replace")
                    if len(content) > _MAX_CONTEXT_FILE_BYTES:
                        content = content[:_MAX_CONTEXT_FILE_BYTES] + "\n... [truncated]"
                    summaries[rel_file] = content
            except Exception:
                continue
        return summaries

    def collect_context_items(self, workspace: WorkspaceContext) -> tuple[ContextSnapshot, ...]:
        """Structured record of the context :meth:`enrich_system_instructions` attaches to a turn.

        Stored on the assistant message so the UI can show what the model was given.
        """
        items: list[ContextSnapshot] = []
        for rel_file, text in self.load_context_summaries(workspace).items():
            items.append(
                ContextSnapshot(
                    kind="file",
                    label=rel_file,
                    path=rel_file,
                    content=text[:600],
                    size_bytes=len(text.encode("utf-8")),
                    truncated=text.endswith("... [truncated]") or len(text) > 600,
                )
            )
        tree_lines: list[str] = []
        self._format_tree_text(self.scan_tree(workspace, max_depth=2), tree_lines)
        items.append(
            ContextSnapshot(
                kind="directory_tree",
                label=f"{workspace.name} (depth 2)",
                path=".",
                content="\n".join(tree_lines),
                size_bytes=len("\n".join(tree_lines).encode("utf-8")),
            )
        )
        return tuple(items)

    def enrich_system_instructions(
        self,
        workspace: WorkspaceContext,
        base_instructions: str = "",
    ) -> str:
        """Inject workspace context, manifest contract, and context files into system prompt."""
        manifest = workspace.manifest
        lines: list[str] = []
        if base_instructions:
            lines.append(base_instructions.strip())
            lines.append("")

        lines.append("## 📦 Active Target Workspace Context")
        lines.append(f"- **Project Name:** {workspace.name} (id: `{workspace.project_id}`)")
        lines.append(f"- **Root Path:** `{workspace.root_path}`")
        lines.append(f"- **Runtime:** `{manifest.runtime}`")
        if manifest.test_command:
            lines.append(f"- **Test Command:** `{manifest.test_command}`")
        if manifest.entrypoint:
            lines.append(f"- **Entrypoint:** `{manifest.entrypoint}`")
        if workspace.is_self:
            lines.append("- **Self-Improvement Mode:** TARGET IS THE META-AGENT APP ITSELF.")

        # Context files
        context_files = self.load_context_summaries(workspace)
        if context_files:
            lines.append("")
            lines.append("### Project Context Documentation")
            for filename, text in context_files.items():
                lines.append(f"#### File: `{filename}`")
                lines.append("```markdown")
                lines.append(text.strip())
                lines.append("```")

        # Tree summary
        tree = self.scan_tree(workspace, max_depth=2)
        lines.append("")
        lines.append("### Directory Structure (Top Level)")
        lines.append("```")
        self._format_tree_text(tree, lines, prefix="")
        lines.append("```")

        return "\n".join(lines)

    def _format_tree_text(self, node: dict[str, Any], output: list[str], prefix: str = "") -> None:
        children = node.get("children", [])
        for i, child in enumerate(children):
            is_last = i == len(children) - 1
            branch = "└── " if is_last else "├── "
            output.append(f"{prefix}{branch}{child['name']}")
            if child.get("type") == "directory" and "children" in child:
                extension = "    " if is_last else "│   "
                self._format_tree_text(child, output, prefix + extension)
