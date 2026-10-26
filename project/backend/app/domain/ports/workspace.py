"""Workspace-related port protocols."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from vibe.domain.models import ProjectManifest


@runtime_checkable
class IManifestParser(Protocol):
    """Protocol for reading and persisting project manifests."""

    def parse(self, path: Path) -> ProjectManifest | None: ...

    def save(self, path: Path, manifest: ProjectManifest) -> Path: ...
