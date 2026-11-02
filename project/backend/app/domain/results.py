"""Execution result and artifact domain models."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import ArtifactKind, TaskStatus
from app.domain.errors import ErrorInfo
from app.domain.usage import Usage


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class Artifact:
    """Artifact produced during agent execution."""

    name: str
    kind: ArtifactKind = ArtifactKind.FILE
    id: str = field(default_factory=new_id)
    mime: str = "text/plain"
    size: int = 0
    inline_content: str | None = None
    content_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "mime": self.mime,
            "size": self.size,
            "inline_content": self.inline_content,
            "content_ref": self.content_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Artifact:
        return cls(
            name=str(data["name"]),
            kind=ArtifactKind(data.get("kind", ArtifactKind.FILE.value)),
            id=str(data.get("id") or new_id()),
            mime=str(data.get("mime", "text/plain")),
            size=int(data.get("size", 0) or 0),
            inline_content=data.get("inline_content"),
            content_ref=data.get("content_ref"),
        )


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Final summary of a completed or failed execution task."""

    task_id: str
    status: TaskStatus
    provider_id: str
    output_text: str = ""
    artifacts: tuple[Artifact, ...] = ()
    usage: Usage = field(default_factory=Usage)
    error: ErrorInfo | None = None
    duration_ms: int = 0
