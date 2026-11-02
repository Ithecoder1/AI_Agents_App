"""Workspace Context & Project Manifest domain models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.errors import ValidationError


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    name: str
    version: str = "0.1.0"
    description: str = ""
    runtime: str = "python:3.10"
    entrypoint: str | None = None
    test_command: str | None = None
    context_files: tuple[str, ...] = ()
    allowed_roots: tuple[str, ...] = ("./",)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "runtime": self.runtime,
            "entrypoint": self.entrypoint,
            "test_command": self.test_command,
            "context_files": list(self.context_files),
            "allowed_roots": list(self.allowed_roots),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProjectManifest:
        context_files = data.get("context_files") or ()
        allowed_roots = data.get("allowed_roots") or ("./",)
        return cls(
            name=str(data.get("name") or "unnamed-project"),
            version=str(data.get("version") or "0.1.0"),
            description=str(data.get("description") or ""),
            runtime=str(data.get("runtime") or "python:3.10"),
            entrypoint=str(data["entrypoint"]) if data.get("entrypoint") else None,
            test_command=str(data["test_command"]) if data.get("test_command") else None,
            context_files=tuple(str(f) for f in context_files),
            allowed_roots=tuple(str(r) for r in allowed_roots),
        )


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    project_id: str
    name: str
    root_path: Path
    manifest: ProjectManifest
    is_self: bool = False

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValidationError("project_id is required")
        if not self.name:
            raise ValidationError("name is required")

    def validate_path(self, target_path: str | Path) -> Path:
        """Validate that target_path resides strictly inside root_path.

        Prevents directory traversal outside the active project boundary.
        Raises ValidationError if outside.
        """
        root = self.root_path.resolve()
        raw = Path(target_path)
        target = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValidationError(
                f"Path traversal detected: {target_path!r} resolves to {target}, "
                f"which is outside workspace root {root}"
            ) from exc
        return target

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "root_path": str(self.root_path.resolve()),
            "manifest": self.manifest.to_dict(),
            "is_self": self.is_self,
        }
