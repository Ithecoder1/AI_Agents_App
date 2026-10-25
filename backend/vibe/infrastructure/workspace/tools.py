"""Workspace-sandboxed execution and file editing tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

from vibe.domain.errors import ValidationError
from vibe.domain.models import WorkspaceContext


def safe_read_file(workspace: WorkspaceContext, rel_path: str | Path) -> str:
    """Read a text file strictly within the workspace root."""
    target = workspace.validate_path(rel_path)
    if not target.is_file():
        raise ValidationError(f"File not found: {rel_path} inside workspace {workspace.name}")
    return target.read_text(encoding="utf-8")


def safe_write_file(workspace: WorkspaceContext, rel_path: str | Path, content: str) -> Path:
    """Write a text file strictly within the workspace root."""
    target = workspace.validate_path(rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def safe_run_command(
    workspace: WorkspaceContext,
    cmd: list[str] | str,
    *,
    timeout_s: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Execute a command strictly within the workspace root working directory."""
    shell = isinstance(cmd, str)
    return subprocess.run(
        cmd,
        cwd=str(workspace.root_path.resolve()),
        shell=shell,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


def safe_git_diff(workspace: WorkspaceContext) -> str:
    """Run git diff within workspace root."""
    try:
        proc = safe_run_command(workspace, ["git", "diff"], timeout_s=15.0)
        return proc.stdout
    except Exception:
        return ""
