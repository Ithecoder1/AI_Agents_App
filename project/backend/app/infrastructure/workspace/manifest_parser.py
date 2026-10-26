"""YAML manifest parser implementing IManifestParser port."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.domain.errors import ConfigurationError
from app.domain.models import ProjectManifest

_MANIFEST_FILENAMES = (
    ".vibe/manifest.yaml",
    ".vibe/manifest.yml",
    ".vibe/project.yaml",
    ".vibe/project.yml",
)


class YamlManifestParser:
    """Parses and writes .vibe/manifest.yaml files."""

    def parse(self, root_path: Path) -> ProjectManifest | None:
        """Find and parse a manifest file under root_path, or return None if absent."""
        for filename in _MANIFEST_FILENAMES:
            manifest_file = root_path / filename
            if manifest_file.is_file():
                try:
                    data = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
                    if not isinstance(data, dict):
                        raise ConfigurationError(
                            f"Manifest {manifest_file} must contain a mapping, "
                            f"got {type(data).__name__}"
                        )
                    return ProjectManifest.from_dict(data)
                except yaml.YAMLError as exc:
                    raise ConfigurationError(
                        f"Failed to parse manifest YAML in {manifest_file}: {exc}"
                    ) from exc
        return None

    def save(self, root_path: Path, manifest: ProjectManifest) -> Path:
        """Persist a ProjectManifest to <root_path>/.vibe/manifest.yaml."""
        target_dir = root_path / ".vibe"
        target_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = target_dir / "manifest.yaml"
        payload: dict[str, Any] = manifest.to_dict()
        manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return manifest_path
