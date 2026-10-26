from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.domain.models import Artifact


class ArtifactStore(Protocol):
    async def put(self, task_id: str, artifact: Artifact, content: bytes) -> Artifact:
        """Store *content*; returns the artifact with ``content_ref``/``size`` filled in."""
        ...

    async def get_content(self, task_id: str, artifact_id: str) -> bytes: ...

    async def list(self, task_id: str) -> Sequence[Artifact]: ...
